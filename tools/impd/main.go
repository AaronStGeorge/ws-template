// impd, the Daemon: the long-lived coordinator that outlives every Client.
//
// One Daemon per workspace: impd runs from the workspace root, reads its
// imp map from an explicit --config file (name -> executable path;
// relative paths resolve from the workspace root, and the file is
// gitignored — see impd.json.example), and owns everything under the
// workspace's .imp/ — socket, lock, and per-Run logs. Per Run it does
// exactly three things (see tools/README.md for the full boundary): exec
// the configured Imp with the Launch Body's arguments, stream its
// stderr into .imp/runs/<run-id>.log, and turn process exit into the Run's
// terminal state. It serves HTTP/JSON on .imp/daemon.sock and interprets
// no argument and no diagnostic, ever; dumbness here is a design
// commitment, not a shortcut.
//
// Run state lives only in memory under one mutex, per the recorded
// no-persistence decision; the per-Run log file is the durable record. The
// imp map is immutable after startup — changing automations means
// editing the config and restarting the Daemon.
package main

import (
	"encoding/json"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"sync"
	"syscall"
)

// config is the whole of impd's configuration: Imp names to executable
// paths, nothing else. Relative paths resolve from the workspace root
// because the Daemon runs with the workspace root as its cwd.
type config struct {
	Imps map[string]string `json:"imps"`
}

// impEntry is the element of GET /v1/imps — a read-only view of
// the configured imp map.
type impEntry struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

// run is both the in-memory record and the JSON document Clients see —
// keeping them one type is what guarantees the API can't drift from the
// state it reports. State moves starting -> running -> succeeded|failed.
type run struct {
	Imp   string  `json:"imp"`
	Id    string  `json:"id"`
	State string  `json:"state"`
	Error *string `json:"error"`
}

// daemon is all the state there is: the immutable name->path table from
// the config, and the Run map. One mutex covers the map and every run in
// it — HTTP handlers and per-Run wait goroutines both touch this, and the
// traffic is far too small to earn anything finer-grained.
type daemon struct {
	imps map[string]string

	mu   sync.Mutex
	runs map[string]*run
}

// The Run Id slug rule lives in the design doc's Language; HTTP input is
// the provenance boundary, so it validates here and nowhere downstream.
var runIdPattern = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

func main() {
	// There is no default config path or search — an explicit --config is
	// the entire startup interface.
	configPath := flag.String("config", "", "path to the imps JSON config")
	flag.Parse()

	raw, err := os.ReadFile(*configPath)
	if err != nil {
		log.Fatal(err)
	}
	var cfg config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		log.Fatal(err)
	}

	// .imp/ holds everything the Daemon writes: the socket, the lock, and
	// the per-Run logs. 0700 on the directory IS the same-user access
	// control — a unix socket no other user can traverse to needs no auth
	// machinery.
	if err := os.MkdirAll(".imp", 0o700); err != nil {
		log.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(".imp", "runs"), 0o700); err != nil {
		log.Fatal(err)
	}

	// One Daemon per workspace, enforced: "a Run Id has at most one Run"
	// only holds while a single process owns the run map, and a second
	// Daemon would silently steal the socket from a live first one. The
	// guard is a nonblocking flock (LOCK_NB — unlike impwatch's rows lock,
	// a rival Daemon must fail fast, not queue up to take over) on a lock
	// file held for the process's lifetime; the kernel releases it on any
	// exit, so a crashed Daemon never wedges the next start. The fd is
	// deliberately kept open and otherwise unused.
	lockFile, err := os.OpenFile(filepath.Join(".imp", "daemon.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		log.Fatal(err)
	}
	if err := syscall.Flock(int(lockFile.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		log.Fatal("another impd already serves this workspace")
	}

	// With the lock held, a leftover socket file can only be the corpse of
	// a dead Daemon — clearing it is safe, and net.Listen refuses an
	// existing path.
	socketPath := filepath.Join(".imp", "daemon.sock")
	os.Remove(socketPath)

	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		log.Fatal(err)
	}

	// The unix socket is the only listener — HTTP over it gives conventional
	// resource + polling semantics without ever opening a network port.
	d := &daemon{imps: cfg.Imps, runs: map[string]*run{}}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/imps", d.handleListImps)
	mux.HandleFunc("POST /v1/runs", d.handleLaunch)
	mux.HandleFunc("GET /v1/runs", d.handleList)
	mux.HandleFunc("GET /v1/runs/{id}", d.handleGet)
	log.Fatal(http.Serve(listener, mux))
}

// handleListImps is GET /v1/imps: a read-only, name-sorted view
// of the configured map (map iteration order isn't deterministic, and a
// listing that shuffled between polls would read as churn).
func (d *daemon) handleListImps(w http.ResponseWriter, _ *http.Request) {
	regs := []impEntry{} // a bare array even when empty, never null
	for name, path := range d.imps {
		regs = append(regs, impEntry{Name: name, Path: path})
	}
	sort.Slice(regs, func(i, j int) bool { return regs[i].Name < regs[j].Name })
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(regs)
}

// handleLaunch is POST /v1/runs — the one write operation the Daemon has.
// The shape of the flow: validate the Launch Body, atomically claim the Run
// Id, wire up the Imp process, and hand the wait to a goroutine so the
// HTTP response never blocks on the work.
func (d *daemon) handleLaunch(w http.ResponseWriter, req *http.Request) {
	// The Launch Body: imp name, Run Id, argv.
	var body struct {
		Imp  string   `json:"imp"`
		Id   string   `json:"id"`
		Args []string `json:"args"`
	}
	if err := json.NewDecoder(req.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	idMatchesSlug := runIdPattern.MatchString(body.Id)
	idFitsLimit := len(body.Id) <= 64
	idIsValid := idMatchesSlug && idFitsLimit
	if !idIsValid {
		http.Error(w, "invalid run id", http.StatusBadRequest)
		return
	}
	impPath, impKnown := d.imps[body.Imp]
	if !impKnown {
		http.Error(w, "unknown imp", http.StatusBadRequest)
		return
	}

	// Claim the Run Id: check-and-insert under the mutex so two concurrent
	// launches of the same Id can't both win. The 409 here is load-bearing —
	// "a Run Id has at most one Run" is what lets Condition Scripts re-emit
	// the same launch every Tick and have duplicates rejected, not re-run.
	d.mu.Lock()
	_, idOccupied := d.runs[body.Id]
	if idOccupied {
		d.mu.Unlock()
		http.Error(w, "run id already occupied", http.StatusConflict)
		return
	}
	r := &run{Imp: body.Imp, Id: body.Id, State: "starting"}
	d.runs[body.Id] = r
	d.mu.Unlock()

	// From here on the Run exists no matter what, so every failure path
	// marks it failed and still answers 202 — a Run must never be left
	// stuck in "starting" with no process behind it.
	logFile, err := os.Create(filepath.Join(".imp", "runs", body.Id+".log"))
	if err != nil {
		d.setState(body.Id, "failed", err.Error())
		d.respondAccepted(w, body.Id)
		return
	}

	// Exec directly — no shell — with the Launch Body's argv. The Imp
	// inherits the Daemon's cwd (the workspace root), which is how relative
	// configured paths and workspace-relative Imps just work. Stderr
	// streams straight into the Run's log file; stdout carries no meaning
	// in the contract and is discarded.
	cmd := exec.Command(impPath, body.Args...)
	cmd.Stderr = logFile
	if err := cmd.Start(); err != nil {
		logFile.Close()
		d.setState(body.Id, "failed", err.Error())
		d.respondAccepted(w, body.Id)
		return
	}
	d.setState(body.Id, "running", "")

	// The wait lives in a goroutine because the Run must outlive this HTTP
	// request — the launching Client may disconnect immediately and the
	// Imp may run for hours. Process exit is the sole outcome signal:
	// zero means succeeded, anything else means failed.
	go func() {
		waitErr := cmd.Wait()
		logFile.Close()
		if waitErr != nil {
			d.setState(body.Id, "failed", waitErr.Error())
		} else {
			d.setState(body.Id, "succeeded", "")
		}
	}()
	d.respondAccepted(w, body.Id)
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

// respondAccepted snapshots the Run under the mutex and writes the 202
// response: Location header plus the Run document.
func (d *daemon) respondAccepted(w http.ResponseWriter, id string) {
	d.mu.Lock()
	snapshot := *d.runs[id]
	d.mu.Unlock()
	w.Header().Set("Location", "/v1/runs/"+id)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(snapshot)
}

// handleList is GET /v1/runs. Reads copy under the mutex and encode outside
// it, so a slow Client can never hold up the wait goroutines' state writes.
func (d *daemon) handleList(w http.ResponseWriter, _ *http.Request) {
	d.mu.Lock()
	runs := []run{} // a bare array even when empty, never null
	for _, r := range d.runs {
		runs = append(runs, *r)
	}
	d.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(runs)
}

// handleGet is GET /v1/runs/{id} — the poll target Clients hit repeatedly
// while waiting for a terminal state.
func (d *daemon) handleGet(w http.ResponseWriter, req *http.Request) {
	id := req.PathValue("id")
	d.mu.Lock()
	r, present := d.runs[id]
	var snapshot run
	if present {
		snapshot = *r
	}
	d.mu.Unlock()
	if !present {
		http.Error(w, "no such run", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(snapshot)
}
