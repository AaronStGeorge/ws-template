// The Watch half of the Daemon: the Watches it holds and the /v1/watches
// handlers over them. The Tick that runs them is tick.go; both files are
// methods on `watches`, which is to Watches and Sensors what `daemon` is to
// Sigils and Imps.
//
// A Watch is a Sensor argv the Tick runs every pass, with a Daemon-assigned
// integer id and a one-shot flag. Ids come from a counter that starts at 1
// and resets with the process, like every other piece of Daemon state, so
// an id is meaningful only against a live Daemon.
//
// The decision this file carries is POST's idempotence: an argv already
// watched with the same one-shot flag returns the existing Watch (200)
// instead of a second one (201). Together with Sigil inscribe's no-op this
// is what makes re-applying a Manifest free. The comparison is exact argv
// equality, deliberately: a Manifest whose argv was edited yields a second
// Watch beside the old one, and the old one is removed by hand
// (`impctl unwatch`), per the documented decision.
//
// `watches` has its own mutex, guarding only the map and the id counter.
// It exists for the HTTP handlers here, which add and remove Watches while
// a pass is running on the other goroutine, and is held only for map
// operations, never across an exec or a launch. The daemon's mutex guards
// Sigils and Runs and nothing here. The two cover disjoint state and the
// dependency runs one way, `watches` calling `daemon.launch` and the daemon
// holding no reference back, so no lock cycle is possible.
//
// HTTP input is the provenance boundary: argv must be non-empty and nothing
// more. argv[0] is not resolved, because an executable that cannot be exec'd
// shows up as a trailer in the watch log, which is where a human looks.
package main

import (
	"encoding/json"
	"net/http"
	"slices"
	"sort"
	"strconv"
	"sync"

	"imp/lib/go/wire"
)

// watches is the Daemon's Watches and the Ticks that run them. It knows
// the daemon only to launch what Sensors emit.
type watches struct {
	d *daemon

	mu          sync.Mutex // byId and nextWatchId; never held across an exec or a launch
	byId        map[int]*wire.Watch
	nextWatchId int // starts at 1; resets with the process

	requests chan tickRequest
}

func newWatches(d *daemon) *watches {
	return &watches{
		d:           d,
		byId:        map[int]*wire.Watch{},
		nextWatchId: 1,
		requests:    make(chan tickRequest),
	}
}

// handleList is GET /v1/watches: id-sorted, which is arming order.
func (w *watches) handleList(rw http.ResponseWriter, _ *http.Request) {
	w.mu.Lock()
	listing := []wire.Watch{} // a bare array even when empty, never null
	for _, entry := range w.byId {
		listing = append(listing, *entry)
	}
	w.mu.Unlock()
	sort.Slice(listing, func(i, j int) bool { return listing[i].Id < listing[j].Id })
	rw.Header().Set("Content-Type", "application/json")
	json.NewEncoder(rw).Encode(listing)
}

// handleWatch is POST /v1/watches: 201 with a new Watch, or 200 with the
// existing one whose argv and one-shot flag both match.
func (w *watches) handleWatch(rw http.ResponseWriter, req *http.Request) {
	var body wire.Watch
	if err := json.NewDecoder(req.Body).Decode(&body); err != nil {
		http.Error(rw, err.Error(), http.StatusBadRequest)
		return
	}
	if len(body.Argv) == 0 {
		http.Error(rw, "watch needs a non-empty argv", http.StatusBadRequest)
		return
	}

	// Find-or-insert under one hold so two concurrent identical POSTs
	// cannot both create.
	w.mu.Lock()
	var existing *wire.Watch
	for _, entry := range w.byId {
		sameArgv := slices.Equal(entry.Argv, body.Argv)
		sameOnce := entry.Once == body.Once
		if sameArgv && sameOnce {
			existing = entry
			break
		}
	}
	status := http.StatusOK
	var result wire.Watch
	if existing != nil {
		result = *existing
	} else {
		created := &wire.Watch{Id: w.nextWatchId, Argv: body.Argv, Once: body.Once}
		w.nextWatchId++
		w.byId[created.Id] = created
		result = *created
		status = http.StatusCreated
	}
	w.mu.Unlock()

	rw.Header().Set("Content-Type", "application/json")
	rw.WriteHeader(status)
	json.NewEncoder(rw).Encode(result)
}

// handleUnwatch is DELETE /v1/watches/{id}: 400 non-integer, 404 absent,
// 204 removed. A Watch removed mid-Tick finishes that pass; the Tick works
// from a snapshot and the drop here is what stops it appearing in the next.
func (w *watches) handleUnwatch(rw http.ResponseWriter, req *http.Request) {
	id, err := strconv.Atoi(req.PathValue("id"))
	if err != nil {
		http.Error(rw, "watch id must be an integer", http.StatusBadRequest)
		return
	}
	w.mu.Lock()
	_, present := w.byId[id]
	delete(w.byId, id)
	w.mu.Unlock()
	if !present {
		http.Error(rw, "no such watch", http.StatusNotFound)
		return
	}
	rw.WriteHeader(http.StatusNoContent)
}
