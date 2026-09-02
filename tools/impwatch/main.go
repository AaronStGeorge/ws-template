// impwatch: the watch Client, the workspace's one sensor.
//
// This file holds the whole program (see tools/README.md for the
// boundary): durable Watch Rows — a Condition Script with its arguments
// plus a clear-after-fire flag — armed over the command line into the
// workspace's .imp/watches.jsonl, a cron-driven one-shot Tick that runs
// every Watch Row's script and POSTs each Launch Body it emits to the
// workspace's Daemon verbatim, and a listing of pending Watch Rows. Run it
// from the workspace root — cwd is how it finds both its rows file and its
// Daemon. It interprets neither conditions nor launches; dumbness here
// mirrors the Daemon's and is a design commitment.
//
// Gotcha for the implementer: deterministic Run Ids are what make emission
// idempotent — a duplicate launch must be treated as the benign, expected
// outcome of a re-fired condition, not an error worth failing the Tick over.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"imp/lib/go/client"
)

// Rows live under the workspace's .imp/ beside the Daemon's socket and
// logs. The file is the durability story: pending watches survive Daemon
// restarts and reboots precisely because they are a file, not a resident
// process's memory.
const rowsPath = ".imp/watches.jsonl"

// watchRow is one durable Watch Row: a Condition Script argv plus the
// clear-after-fire flag. It is deliberately not a launch — the launch is
// whatever the script emits when its condition holds, which is what lets a
// standing row parametrize work it discovers (a fresh Run Id per red PR).
type watchRow struct {
	Argv  []string `json:"argv"`
	Clear bool     `json:"clear"`
}

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	switch os.Args[1] {
	case "arm":
		arm(os.Args[2:])
	case "list":
		list()
	case "tick":
		tick()
	default:
		usage()
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: impwatch arm [--clear] -- ARGV... | impwatch list | impwatch tick")
	os.Exit(2)
}

// lockRows opens the rows file (creating it on first use) and takes the
// blocking advisory lock that serializes every reader and writer of it.
// Plain LOCK_EX — no LOCK_NB — because blocking is the wanted behavior: an
// arm racing a Tick waits out the Tick's few seconds instead of erroring,
// which is what closes the lost-update race (a Tick's end-of-pass rewrite
// silently erasing a row armed after its read). The kernel releases the
// lock when the fd closes, process death included, so a crash cannot wedge
// the file. The one deadlock cycle this design must never create: a
// Condition Script invoking `impwatch arm` — it would block on the very
// Tick that is waiting for it. Scripts emit launches; only Imps and
// the human arm.
func lockRows() *os.File {
	if err := os.MkdirAll(filepath.Dir(rowsPath), 0o700); err != nil {
		log.Fatal(err)
	}
	f, err := os.OpenFile(rowsPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		log.Fatal(err)
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		log.Fatal(err)
	}
	return f
}

// arm appends one Watch Row — the whole arming interface; there is no
// standing configuration file. Under the lock, seek-to-end-and-write is a
// race-free append.
func arm(args []string) {
	fs := flag.NewFlagSet("arm", flag.ExitOnError)
	clear := fs.Bool("clear", false, "clear the row after it fires")
	fs.Parse(args) // "--" ends flags; fs.Args() holds the Condition Script argv
	row := watchRow{Argv: fs.Args(), Clear: *clear}
	line, err := json.Marshal(row)
	if err != nil {
		log.Fatal(err)
	}
	f := lockRows()
	defer f.Close()
	if _, err := f.Seek(0, io.SeekEnd); err != nil {
		log.Fatal(err)
	}
	if _, err := f.Write(append(line, '\n')); err != nil {
		log.Fatal(err)
	}
}

// list also takes the lock — briefly — so it can never observe a Tick's
// rewrite half-written.
func list() {
	f := lockRows()
	defer f.Close()
	for _, line := range readRowLines(f) {
		fmt.Println(line)
	}
}

// tick is the one-shot evaluation pass: read every row, run its Condition
// Script, relay what it emits, and rewrite the rows file once at the end.
// Clearing works by omission — a row that should drop is simply not written
// back — which is why the rewrite happens exactly once, after all rows ran.
// The lock is held across the whole pass, scripts included: read and
// rewrite must be one atomic unit or a concurrent arm's row is lost to the
// snapshot rewrite. Armers just block for the Tick's few seconds.
func tick() {
	f := lockRows()
	defer f.Close()
	lines := readRowLines(f)
	if len(lines) == 0 {
		return
	}
	// The watch log is to Condition Scripts what .imp/runs/<id>.log is to
	// Imps: where their stderr lands. One shared append-mode file —
	// scripts have no Run Id to name per-run files by, and a Tick's worth
	// of diagnostics reads fine interleaved with its argv+exit trailers.
	watchLog, err := os.OpenFile(filepath.Join(".imp", "watch.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		log.Fatal(err)
	}
	defer watchLog.Close()
	c := client.New()
	var kept []string
	for _, line := range lines {
		// A row that won't parse is logged and kept as-is: the tick never
		// destroys what it doesn't understand.
		var row watchRow
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			log.Print(err)
			kept = append(kept, line)
			continue
		}
		fired := runRow(c, row, watchLog)
		// Recorded MVP decision: a clear row that fired is dropped
		// regardless of delivery errors — firing, not delivery, is the
		// clearing trigger, so a flaky Daemon can't make a one-shot watch
		// fire twice.
		dropRow := row.Clear && fired
		if !dropRow {
			kept = append(kept, line)
		}
	}
	// Rewrite in place through the locked fd (truncate + write from the
	// start) — the inode keeps its lock, and no reader holds the lock to
	// see the intermediate empty state.
	if err := f.Truncate(0); err != nil {
		log.Fatal(err)
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		log.Fatal(err)
	}
	if _, err := f.WriteString(joinLines(kept)); err != nil {
		log.Fatal(err)
	}
}

// runRow execs one Condition Script and POSTs each emitted Launch Body;
// reports whether the row fired (emitted at least one nonempty line).
func runRow(c *client.Client, row watchRow, watchLog *os.File) bool {
	// Direct argv exec, no shell. The channel split mirrors the Imp
	// Process Contract: stdout is the contract (Launch Bodies), stderr is
	// diagnostics streamed into the watch log — but unlike a Imp, the
	// exit code carries no meaning to impwatch. It is appended to the log
	// for the human and nothing else, so firing is defined by emission
	// alone: a script that emits a launch and then dies has still fired.
	var stdout bytes.Buffer
	cmd := exec.Command(row.Argv[0], row.Argv[1:]...)
	cmd.Stdout = &stdout
	cmd.Stderr = watchLog
	runErr := cmd.Run()
	execNeverRan := cmd.ProcessState == nil // e.g. the executable is missing
	exitCode := -1
	if !execNeverRan {
		exitCode = cmd.ProcessState.ExitCode()
	}
	if runErr != nil && execNeverRan {
		fmt.Fprintf(watchLog, "%v\n", runErr)
	}
	fmt.Fprintf(watchLog, "%s: exit %d\n", strings.Join(row.Argv, " "), exitCode)

	fired := false
	scanner := bufio.NewScanner(&stdout)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		fired = true
		var body client.LaunchBody
		if err := json.Unmarshal([]byte(line), &body); err != nil {
			log.Print(err)
			continue
		}
		// The relay POSTs the emission verbatim and interprets nothing.
		// A 409 is the design working: deterministic Run Ids make
		// re-emission a rejected duplicate, never duplicated work.
		_, err := c.Launch(body)
		launchRejectedAsDuplicate := errors.Is(err, client.ErrDuplicateRun)
		if err != nil && !launchRejectedAsDuplicate {
			log.Print(err) // logged and nothing more, per the MVP decision
		}
	}
	return fired
}

// readRowLines returns the nonempty lines of the locked rows file (empty on
// first use — lockRows creates it).
func readRowLines(f *os.File) []string {
	raw, err := io.ReadAll(f)
	if err != nil {
		log.Fatal(err)
	}
	var lines []string
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

func joinLines(lines []string) string {
	if len(lines) == 0 {
		return ""
	}
	return strings.Join(lines, "\n") + "\n"
}
