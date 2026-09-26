// The Tick: one pass over every Watch, on the interval and on request.
// Methods on `watches` (see watches.go for the struct and its handlers).
//
// A Tick runs each Watch's Sensor argv (no shell), parses each nonempty
// stdout line as a Launch, and hands it to the daemon's launch(). The
// Sensor's stderr is appended to .imp/watch.log, followed by a trailer
// `<argv>: exit N`, so a human reading the log sees each Sensor's
// diagnostics and how it ended. That is the whole of a Tick; it interprets
// nothing a Sensor says beyond parsing the JSON.
//
// Three decisions shape this file. First, a duplicate Run Id from launch()
// is the benign, expected outcome of a re-fired condition, counted and
// never logged as a fault: deterministic Run Ids make re-emission
// idempotent. Second, a one-shot Watch is dropped after the pass in which
// it first emitted; "fired" is defined by emission alone, not by the launch
// succeeding, so a one-shot can never fire twice. Third, the pass takes a
// snapshot of the Watches and releases the mutex before exec'ing anything:
// Sensors call `gh` and take seconds, and the HTTP handlers must stay
// responsive throughout.
//
// Passes never overlap because exactly one goroutine, run, runs them: the
// interval and Client requests are both just events it selects on, and a
// requested pass waits its turn on the request channel. The mutex taken
// for the snapshot and the drop is not about other passes; it is about the
// handlers in watches.go adding and removing Watches on their own
// goroutines while a pass is under way. There is no pass at startup: the
// Daemon starts empty.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"sort"
	"strings"
	"time"

	"imp/lib/go/wire"
)

// tickInterval is the compile-time default; per-Watch schedules are not a
// thing, and time-slot logic belongs in the Sensor.
const tickInterval = 300 * time.Second

// tickRequest is a Client asking for a pass; the summary comes back on
// reply once the pass is done.
type tickRequest struct {
	reply chan wire.TickSummary
}

// run is the one goroutine that runs passes: one every tickInterval, plus
// one per request. A request arriving mid-pass waits on the channel send
// until this loop comes back to select, which is how requested and
// interval passes queue instead of overlapping. Deliberately no immediate
// pass: the first one is up to tickInterval after startup, and
// `impctl tick` is the door for sooner.
func (w *watches) run() {
	ticker := time.NewTicker(tickInterval)
	for {
		select {
		case <-ticker.C:
			w.tick()
		case req := <-w.requests:
			req.reply <- w.tick()
		}
	}
}

// tick is one pass, called only from run. The flow: snapshot the Watches
// under mu, release it, run each Sensor with mu free, then re-take mu to
// drop the one-shots that fired. Dropping by id makes a concurrent unwatch
// harmless (deleting a missing key is a no-op).
func (w *watches) tick() wire.TickSummary {
	w.mu.Lock()
	pass := make([]wire.Watch, 0, len(w.byId))
	for _, entry := range w.byId {
		pass = append(pass, *entry)
	}
	w.mu.Unlock()
	sort.Slice(pass, func(i, j int) bool { return pass[i].Id < pass[j].Id })

	summary := wire.TickSummary{Watches: len(pass)}
	if len(pass) == 0 {
		return summary
	}

	// The watch log is to Sensors what .imp/runs/<id>.log is to Imps: where
	// their stderr lands. One shared append-mode file, opened per pass,
	// because Sensors have no Run Id to name per-run files by and a pass's
	// diagnostics read fine interleaved with their trailers.
	watchLog, err := os.OpenFile(wire.WatchLogPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		log.Print(err)
		return summary
	}
	defer watchLog.Close()

	var dropped []int
	for _, watch := range pass {
		fired, launched, duplicates := w.runSensor(watch, watchLog)
		summary.Launched += launched
		summary.Duplicates += duplicates
		oneShotFired := watch.Once && fired
		if oneShotFired {
			dropped = append(dropped, watch.Id)
		}
	}

	w.mu.Lock()
	for _, id := range dropped {
		delete(w.byId, id)
	}
	w.mu.Unlock()
	summary.Dropped = len(dropped)
	return summary
}

// runSensor execs one Watch's Sensor and launches each Launch it emits.
// fired means at least one nonempty stdout line, whatever became of it.
// The channel split mirrors the Imp contract: stdout is the contract
// (Launches), stderr is diagnostics streamed into the watch log. The exit
// code carries no meaning to the Daemon; it is appended to the log for the
// human and nothing else, so a Sensor that emits a Launch and then dies has
// still fired.
func (w *watches) runSensor(watch wire.Watch, watchLog *os.File) (fired bool, launched, duplicates int) {
	var stdout bytes.Buffer
	cmd := exec.Command(watch.Argv[0], watch.Argv[1:]...)
	cmd.Stdout = &stdout
	cmd.Stderr = watchLog
	runErr := cmd.Run()
	execNeverRan := cmd.ProcessState == nil // e.g. the executable is missing
	exitCode := -1
	if !execNeverRan {
		exitCode = cmd.ProcessState.ExitCode()
	}
	failedBeforeRunning := runErr != nil && execNeverRan
	if failedBeforeRunning {
		fmt.Fprintf(watchLog, "%v\n", runErr)
	}
	fmt.Fprintf(watchLog, "%s: exit %d\n", strings.Join(watch.Argv, " "), exitCode)

	scanner := bufio.NewScanner(&stdout)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		fired = true
		var emitted wire.Launch
		if err := json.Unmarshal([]byte(line), &emitted); err != nil {
			fmt.Fprintf(watchLog, "watch %d: unparseable launch: %v\n", watch.Id, err)
			continue
		}
		// Every emission goes through the same launch() as a Client's
		// POST, verbatim. A duplicate is the design working: the same Run
		// Id re-emitted is rejected, never duplicated work.
		_, err := w.d.launch(emitted)
		switch {
		case err == nil:
			launched++
		case errors.Is(err, errDuplicateRun):
			duplicates++
		default:
			fmt.Fprintf(watchLog, "watch %d: launch %q rejected: %v\n", watch.Id, emitted.Id, err)
		}
	}
	return fired, launched, duplicates
}

// handleTick is POST /v1/tick: one pass, synchronous, answering with the
// summary once the pass is done. Tests and "I edited a Sensor, fire it
// now" come through here. The handler never runs a pass itself; it hands
// the request to run and waits, which is what keeps passes from
// overlapping. There is no cycle to deadlock on: run never waits on a
// handler.
func (w *watches) handleTick(rw http.ResponseWriter, _ *http.Request) {
	reply := make(chan wire.TickSummary, 1)
	w.requests <- tickRequest{reply: reply}
	summary := <-reply
	rw.Header().Set("Content-Type", "application/json")
	json.NewEncoder(rw).Encode(summary)
}
