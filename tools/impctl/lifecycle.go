// The lifecycle verbs: `up`, `down`, `status`. impctl is the Daemon's
// supervisor in the client-managed-daemon idiom (Bazel, adb, watchman):
// impd never detaches, so `up` spawns it in its own session and waits for
// it, `down` signals it and waits for it to be gone, and `status` reports
// which of three states it is in.
//
// Liveness is the Daemon's single-instance flock on .imp/daemon.lock,
// probed nonblocking, and nothing else. The kernel releases an flock on
// any process death, SIGKILL included, so a probe that acquires the lock
// is proof the Daemon is gone; a pid or cmdline check could be fooled by
// a recycled pid and is never consulted. The probe must close its fd the
// moment it acquires, or impctl itself would be the Daemon nobody can
// start. Readiness is a successful dial of the socket: the socket file
// alone proves nothing, because a killed Daemon leaves it behind.
//
// The lock file's contents are the holder's pid, written by impd right
// after it takes the lock, and exist only so `down` has a pid to SIGTERM,
// which also makes a Daemon started by hand stoppable the same way. One
// file with one writer means there is no "lock held but no pid" state to
// handle and nothing for impctl to ever remove; a pid read while the lock
// is free is stale by definition and is never used.
//
// `up` finds impd beside its own executable: the two are built together
// into one bin directory, so no PATH lookup or configuration is needed.
// impd's stdout and stderr are appended to .imp/impd.out, which is where
// to look when `up` reports the Daemon exited or never accepted.
package main

import (
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"imp/lib/go/client"
	"imp/lib/go/wire"
)

const (
	daemonOut = ".imp/impd.out"

	readyTimeout = 10 * time.Second
	stopTimeout  = 10 * time.Second
	probeTimeout = 200 * time.Millisecond
	pollInterval = 100 * time.Millisecond
	logTailLines = 20
)

// stringList is a repeatable string flag (`--manifest A --manifest B`).
type stringList []string

func (l *stringList) String() string     { return strings.Join(*l, ",") }
func (l *stringList) Set(v string) error { *l = append(*l, v); return nil }

// up starts the Daemon if it is not running, waits for it to accept, and
// applies each Manifest in the order given. Rerunning it is free.
func up(args []string) {
	fs := flag.NewFlagSet("up", flag.ExitOnError)
	var manifests stringList
	fs.Var(&manifests, "manifest", "Manifest to apply once the Daemon is ready (repeatable)")
	fs.Parse(args)
	if len(fs.Args()) != 0 {
		usage()
	}
	if err := ensureDaemon(); err != nil {
		log.Fatal(err)
	}
	c := client.New()
	for _, path := range manifests {
		if err := applyManifest(c, path); err != nil {
			log.Fatal(err)
		}
	}
}

// ensureDaemon leaves a Daemon accepting on the socket, spawning one if
// the lock is free. When the lock is held the Daemon is someone else's
// (an earlier `up`, or a hand start) and the only job is to wait for its
// socket; a Daemon that holds the lock but never accepts is wedged, and
// the error names its pid so a human can deal with it.
func ensureDaemon() error {
	free, err := lockIsFree()
	if err != nil {
		return err
	}
	if !free {
		pid, err := readPid()
		if err != nil {
			return fmt.Errorf("impd holds %s but its pid could not be read: %w", wire.LockPath, err)
		}
		fmt.Printf("impd already running (pid %d)\n", pid)
		_, err = waitForSocket(nil, readyTimeout)
		if err != nil {
			return fmt.Errorf("impd (pid %d) holds %s but its socket never accepted within %s", pid, wire.LockPath, readyTimeout)
		}
		return nil
	}

	cmd, err := spawnDaemon()
	if err != nil {
		return err
	}
	// Reaping in a goroutine is what lets waitForSocket notice a Daemon
	// that died before accepting (a second impd losing the lock race, a
	// startup fatal) instead of running out the clock on it.
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()
	ready, err := waitForSocket(exited, readyTimeout)
	if err != nil {
		return fmt.Errorf("impd did not come up: %w; see %s", err, daemonOut)
	}
	fmt.Printf("impd ready in %s (pid %d)\n", ready.Round(time.Millisecond), cmd.Process.Pid)
	return nil
}

// down SIGTERMs the Daemon and waits for its lock to free. Runs in flight
// are orphaned and continue; `status` is where to see them beforehand.
// Nothing is removed: the lock file stays, and its pid means nothing once
// the lock is free.
func down() {
	free, err := lockIsFree()
	if err != nil {
		log.Fatal(err)
	}
	if free {
		fmt.Println("impd not running")
		return
	}
	pid, err := readPid()
	if err != nil {
		log.Fatalf("impd holds %s but its pid could not be read: %v", wire.LockPath, err)
	}
	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
		log.Fatalf("SIGTERM pid %d: %v", pid, err)
	}
	deadline := time.Now().Add(stopTimeout)
	for {
		free, err := lockIsFree()
		if err != nil {
			log.Fatal(err)
		}
		if free {
			break
		}
		if time.Now().After(deadline) {
			log.Fatalf("impd (pid %d) still holds %s %s after SIGTERM", pid, wire.LockPath, stopTimeout)
		}
		time.Sleep(pollInterval)
	}
	fmt.Printf("impd stopped (pid %d)\n", pid)
}

// status reports one of three states, then everything the Daemon holds
// and the tail of the watch log. `starting-or-wedged` is lock held but
// socket not accepting: a Daemon between taking the lock and listening,
// or one that will never listen; a second `status` a moment later tells
// which.
func status() {
	free, err := lockIsFree()
	if err != nil {
		log.Fatal(err)
	}
	if free {
		fmt.Println("impd: down")
		return
	}
	pidLabel := "pid unknown"
	if pid, err := readPid(); err == nil {
		pidLabel = fmt.Sprintf("pid %d", pid)
	}
	if !socketAccepts() {
		fmt.Printf("impd: starting-or-wedged (%s)\n", pidLabel)
		return
	}
	fmt.Printf("impd: up (%s)\n", pidLabel)

	c := client.New()
	sigils, err := c.ListSigils()
	if err != nil {
		log.Fatal(err)
	}
	watches, err := c.ListWatches()
	if err != nil {
		log.Fatal(err)
	}
	runs, err := c.ListRuns()
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("\n== sigils")
	for _, s := range sigils {
		printJSON(s)
	}
	fmt.Println("\n== watches")
	for _, w := range watches {
		printJSON(w)
	}
	fmt.Println("\n== runs")
	for _, r := range runs {
		printJSON(r)
	}
	fmt.Printf("\n== tail of %s\n", wire.WatchLogPath)
	for _, line := range tailLines(wire.WatchLogPath, logTailLines) {
		fmt.Println(line)
	}
}

// lockIsFree probes the Daemon's flock nonblocking. Acquiring it proves no
// Daemon holds it; the fd is closed at once so the probe releases what it
// took. Creating the lock file (and .imp/) on a fresh workspace is fine:
// the Daemon opens the same path with O_CREATE.
func lockIsFree() (bool, error) {
	if err := os.MkdirAll(wire.ImpDir, 0o700); err != nil {
		return false, err
	}
	f, err := os.OpenFile(wire.LockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return false, err
	}
	defer f.Close()
	err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if err == nil {
		return true, nil
	}
	if errors.Is(err, syscall.EWOULDBLOCK) {
		return false, nil
	}
	return false, err
}

// socketAccepts is the readiness probe: a completed dial and nothing less.
func socketAccepts() bool {
	conn, err := net.DialTimeout("unix", wire.SocketPath, probeTimeout)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// spawnDaemon starts impd detached: its own session so a terminal's
// SIGHUP never reaches it, stdin closed, stdout and stderr appended to
// .imp/impd.out. Only stdio is passed; the Daemon takes its own lock.
func spawnDaemon() (*exec.Cmd, error) {
	self, err := os.Executable()
	if err != nil {
		return nil, err
	}
	out, err := os.OpenFile(daemonOut, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	// The parent's copy of the log fd is closed once the child holds its
	// own; the child keeps the file open for its lifetime.
	defer out.Close()
	cmd := exec.Command(filepath.Join(filepath.Dir(self), "impd"))
	cmd.Stdout = out
	cmd.Stderr = out
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return cmd, nil
}

// waitForSocket polls until the socket accepts, returning the time it
// took. exited, when non-nil, reports the spawned Daemon's early death so
// the wait ends with its reason rather than a timeout; a nil channel is
// never ready, which is the wanted behavior for a Daemon impctl did not
// spawn.
func waitForSocket(exited <-chan error, timeout time.Duration) (time.Duration, error) {
	start := time.Now()
	deadline := start.Add(timeout)
	for {
		if socketAccepts() {
			return time.Since(start), nil
		}
		select {
		case err := <-exited:
			return 0, fmt.Errorf("impd exited before accepting: %v", err)
		default:
		}
		if time.Now().After(deadline) {
			return 0, fmt.Errorf("socket did not accept within %s", timeout)
		}
		time.Sleep(pollInterval)
	}
}

// readPid reads the pid impd wrote into the lock file after taking the
// lock. Only meaningful while the lock is held; callers check that first.
func readPid() (int, error) {
	raw, err := os.ReadFile(wire.LockPath)
	if err != nil {
		return 0, err
	}
	return strconv.Atoi(strings.TrimSpace(string(raw)))
}

// tailLines returns the last n lines of a file, or nothing if it is absent.
func tailLines(path string, n int) []string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return lines
}
