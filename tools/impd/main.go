// impd, the Daemon: the one long-lived process in a workspace, holding
// every Sigil, Watch, and Run, running Ticks, and launching Imps.
//
// One Daemon per workspace: impd runs in the foreground from the workspace
// root, takes no flags, starts empty, and owns everything under the
// workspace's .imp/: the single-instance lock (whose contents are its
// pid), the socket, per-Run logs, and the watch log. Everything it knows arrives over the
// socket, one imperative call each (see tools/README.md for the API table
// and docs/imp-design.md for the Language); it reads no configuration file
// and interprets no argument, no diagnostic, and no Manifest. Dumbness here
// is a design commitment, not a shortcut.
//
// This file is the process skeleton plus the Sigil-and-Run half of the
// Daemon: startup, the shared launch() path, the /v1/runs handlers, and
// shutdown. Sigils have their own file; the Watch half is `watches`
// (watches.go for the store and handlers, tick.go for the Tick).
//
// All state is in memory, per the recorded no-persistence decision: the
// per-Run log files and the watch log are the durable record, and a
// restart forgets every Run Id (so a Sensor that re-emits a past Run Id
// after a restart re-runs it; accepted). There are two mutexes over
// disjoint state, each held only for a map operation: this daemon's over
// Sigils and Runs, and `watches`'s over the Watches. The dependency runs
// one way (`watches` calls launch(); nothing here references `watches`),
// so no lock cycle is possible. Tick passes, which spend seconds inside
// Sensors, are serialized by `watches`'s single goroutine, not by a lock.
//
// launch() is the one door every Run goes through, whether the Launch came
// over HTTP or out of a Sensor's stdout, and it is where the Launch is
// validated, so the Tick can hand emissions straight to it.
// The Sigil is consulted only inside launch(): from exec onward the Run is
// an independent process whose record carries the path it actually ran.
package main

import (
	"encoding/json"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"sync"
	"syscall"
	"time"

	"imp/lib/go/wire"
)

const runsDir = ".imp/runs"

// The lock is retried briefly because impctl's liveness probe takes this
// same exclusive flock for a few microseconds and releases it; a Daemon
// starting in that window must not mistake the probe for a rival. A real
// rival holds the lock for its whole life and still fails these attempts.
const (
	lockAttempts = 10
	lockRetryGap = 20 * time.Millisecond
)

// daemon is the Sigil-and-Run half of the state. One mutex covers both
// maps and every run in them: handlers, wait goroutines, and the Tick's
// launches all touch this, and the traffic is far too small to earn
// anything finer-grained. The Watches live in `watches`, under its own
// mutex.
type daemon struct {
	mu     sync.Mutex
	sigils map[string]string
	runs   map[string]*wire.Run
}

// The lock file is held for the whole process lifetime by staying
// referenced here: an *os.File that falls out of scope is finalized, its
// fd closed, and the lock silently released.
var lockFile *os.File

// The slug rule lives in the design doc's Language for Run Ids; Sigil
// names follow it too, because a name is a URL path segment
// (/v1/sigils/{name}) and anything looser would leave a Sigil that cannot
// be erased.
var slugPattern = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

// The launch() failure modes, distinguished so the HTTP handler can map
// them to statuses and the Tick can tell a benign duplicate from a fault.
var (
	errInvalidRunId = errors.New("invalid run id")
	errUnknownSigil = errors.New("unknown sigil")
	errDuplicateRun = errors.New("run id already occupied")
)

func main() {
	// .imp/ holds everything the Daemon writes. 0700 on the directory IS
	// the same-user access control: a unix socket no other user can
	// traverse to needs no auth machinery.
	if err := os.MkdirAll(wire.ImpDir, 0o700); err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(runsDir, 0o700); err != nil {
		log.Fatal(err)
	}

	// One Daemon per workspace, enforced: "a Run Id has at most one Run"
	// only holds while a single process owns the run map, and a second
	// Daemon would silently steal the socket from a live first one. The
	// guard is a nonblocking flock on a file held for the process's
	// lifetime; the kernel releases it on any exit, SIGKILL included, so a
	// crashed Daemon never wedges the next start, and impctl probes the
	// same lock as its only liveness signal.
	var err error
	lockFile, err = os.OpenFile(wire.LockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		log.Fatal(err)
	}
	if err := acquireLock(lockFile); err != nil {
		log.Fatal("another impd already serves this workspace")
	}

	// The lock file's contents are the holder's pid, so `impctl down` has
	// a pid to SIGTERM. Writing it into the locked file rather than a
	// second file means there is exactly one writer and no moment where
	// the lock is held but the pid is missing; whatever a dead Daemon left
	// in here is overwritten and was never consulted for liveness anyway.
	if err := lockFile.Truncate(0); err != nil {
		log.Fatal(err)
	}
	if _, err := lockFile.WriteAt([]byte(strconv.Itoa(os.Getpid())+"\n"), 0); err != nil {
		log.Fatal(err)
	}

	// With the lock held, a leftover socket file can only be the corpse of
	// a dead Daemon; clearing it is safe, and net.Listen refuses an
	// existing path.
	os.Remove(wire.SocketPath)
	listener, err := net.Listen("unix", wire.SocketPath)
	if err != nil {
		log.Fatal(err)
	}

	d := &daemon{
		sigils: map[string]string{},
		runs:   map[string]*wire.Run{},
	}
	ws := newWatches(d)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/sigils", d.handleListSigils)
	mux.HandleFunc("POST /v1/sigils", d.handleInscribe)
	mux.HandleFunc("DELETE /v1/sigils/{name}", d.handleErase)
	mux.HandleFunc("GET /v1/watches", ws.handleList)
	mux.HandleFunc("POST /v1/watches", ws.handleWatch)
	mux.HandleFunc("DELETE /v1/watches/{id}", ws.handleUnwatch)
	mux.HandleFunc("GET /v1/runs", d.handleListRuns)
	mux.HandleFunc("POST /v1/runs", d.handleLaunch)
	mux.HandleFunc("GET /v1/runs/{id}", d.handleGetRun)
	mux.HandleFunc("POST /v1/tick", ws.handleTick)

	// The interval loop runs no pass at startup: the Daemon starts empty,
	// so there would be nothing to run. `impctl tick` is the door for an
	// earlier first pass.
	go ws.run()
	go func() {
		// Serve returning is fatal unless it is our own Close on the way
		// out: a Daemon whose accept loop has died but which still holds
		// the lock would be permanently "starting-or-wedged", blocking
		// every future `up`. Dying releases the lock and lets the next
		// `up` start clean.
		err := http.Serve(listener, mux)
		closedByShutdown := errors.Is(err, net.ErrClosed)
		if !closedByShutdown {
			log.Fatal(err)
		}
	}()

	// Shutdown on SIGTERM/SIGINT is deliberately abrupt: close the door,
	// remove the socket file, exit. Runs in flight are orphaned and
	// continue (their logs keep streaming to the files they hold); a Tick
	// in progress is abandoned, since waiting for it would hold
	// `impctl down` hostage to a Sensor. The lock file stays:
	// its flock is released by the exit and its stale pid means nothing
	// once the lock is free.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	<-stop
	listener.Close()
	os.Remove(wire.SocketPath)
	os.Exit(0)
}

// acquireLock takes the single-instance flock, retrying through the
// microsecond window in which an impctl probe holds it. Any error other
// than EWOULDBLOCK is returned at once.
func acquireLock(f *os.File) error {
	var err error
	for attempt := 0; attempt < lockAttempts; attempt++ {
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return nil
		}
		heldByAnother := errors.Is(err, syscall.EWOULDBLOCK)
		if !heldByAnother {
			return err
		}
		time.Sleep(lockRetryGap)
	}
	return err
}

// launch is the shared door for every Run. The shape of the flow: validate
// the Launch, atomically resolve the Sigil and claim the Run Id, wire up
// the Imp process, and hand the wait to a goroutine so no caller blocks on
// the work. The returned Run is its state as of the return. The parameter
// is `body` because it is literally the request body, and `launch` is this
// method.
func (d *daemon) launch(body wire.Launch) (wire.Run, error) {
	idMatchesSlug := slugPattern.MatchString(body.Id)
	idFitsLimit := len(body.Id) <= 64
	idIsValid := idMatchesSlug && idFitsLimit
	if !idIsValid {
		return wire.Run{}, errInvalidRunId
	}

	// Resolve and claim under one hold: the Sigil is read before the id is
	// claimed so an unknown name never burns a Run Id, and the Path lands
	// in the record in the same hold so a concurrent re-inscribe can't
	// make the record disagree with what gets exec'd. The duplicate check
	// here is load-bearing: "a Run Id has at most one Run" is what lets
	// Sensors re-emit the same Launch every Tick and have duplicates
	// rejected, not re-run.
	d.mu.Lock()
	path, sigilKnown := d.sigils[body.Sigil]
	if !sigilKnown {
		d.mu.Unlock()
		return wire.Run{}, errUnknownSigil
	}
	_, idOccupied := d.runs[body.Id]
	if idOccupied {
		d.mu.Unlock()
		return wire.Run{}, errDuplicateRun
	}
	d.runs[body.Id] = &wire.Run{Sigil: body.Sigil, Id: body.Id, Path: path, State: "starting"}
	d.mu.Unlock()

	// From here on the Run exists no matter what, so every failure path
	// marks it failed and still returns the Run: a Run must never be
	// left stuck in "starting" with no process behind it.
	logFile, err := os.Create(filepath.Join(runsDir, body.Id+".log"))
	if err != nil {
		d.setState(body.Id, "failed", err.Error())
		return d.snapshot(body.Id), nil
	}

	// Exec directly, no shell, with the Launch's args as argv. The Imp
	// inherits the Daemon's cwd (the workspace root), which is how
	// workspace-relative Sigil paths just work. Stderr streams straight
	// into the Run's log file; stdout carries no meaning in the contract
	// and is discarded. A bad path is a failed Run, not a rejected launch.
	cmd := exec.Command(path, body.Args...)
	cmd.Stderr = logFile
	if err := cmd.Start(); err != nil {
		logFile.Close()
		d.setState(body.Id, "failed", err.Error())
		return d.snapshot(body.Id), nil
	}
	d.setState(body.Id, "running", "")

	// The wait lives in a goroutine because the Run must outlive its
	// launcher: a Client may disconnect immediately, a Tick moves on to the
	// next Watch, and the Imp may run for hours. Process exit is the sole
	// outcome signal: zero means succeeded, anything else means failed.
	go func() {
		waitErr := cmd.Wait()
		logFile.Close()
		if waitErr != nil {
			d.setState(body.Id, "failed", waitErr.Error())
		} else {
			d.setState(body.Id, "succeeded", "")
		}
	}()
	return d.snapshot(body.Id), nil
}

// handleLaunch is POST /v1/runs, the manual door for a Client's own
// Launch. HTTP input is the provenance boundary, but launch() owns the
// checks, so this handler only decodes and maps outcomes to statuses.
func (d *daemon) handleLaunch(w http.ResponseWriter, req *http.Request) {
	var body wire.Launch
	if err := json.NewDecoder(req.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	r, err := d.launch(body)
	switch {
	case errors.Is(err, errInvalidRunId), errors.Is(err, errUnknownSigil):
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	case errors.Is(err, errDuplicateRun):
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	w.Header().Set("Location", "/v1/runs/"+r.Id)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(r)
}

// setState moves a Run to a new state; errText "" means no error.
func (d *daemon) setState(id, state, errText string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	r := d.runs[id]
	r.State = state
	if errText == "" {
		r.Error = nil
	} else {
		r.Error = &errText
	}
}

// snapshot copies a Run under the mutex, for encoding outside it.
func (d *daemon) snapshot(id string) wire.Run {
	d.mu.Lock()
	defer d.mu.Unlock()
	return *d.runs[id]
}

// handleListRuns is GET /v1/runs. Reads copy under the mutex and encode
// outside it, so a slow Client can never hold up the wait goroutines'
// state writes.
func (d *daemon) handleListRuns(w http.ResponseWriter, _ *http.Request) {
	d.mu.Lock()
	runs := []wire.Run{} // a bare array even when empty, never null
	for _, r := range d.runs {
		runs = append(runs, *r)
	}
	d.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(runs)
}

// handleGetRun is GET /v1/runs/{id}, the poll target Clients hit
// repeatedly while waiting for a terminal state.
func (d *daemon) handleGetRun(w http.ResponseWriter, req *http.Request) {
	id := req.PathValue("id")
	d.mu.Lock()
	r, present := d.runs[id]
	var doc wire.Run
	if present {
		doc = *r
	}
	d.mu.Unlock()
	if !present {
		http.Error(w, "no such run", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(doc)
}
