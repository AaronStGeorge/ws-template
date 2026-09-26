// The Sigil half of the Daemon: /v1/sigils.
//
// A Sigil binds a name to an executable path; it is consulted only inside
// launch(), so nothing here ever touches a Run. The one decision this file
// carries is the inscribe outcome: same name at the same path is a no-op
// (`unchanged`), at a different path it replaces and says so (`replaced`),
// otherwise `created`. That triple is what makes re-applying a Manifest
// free while still telling the human when a path moved under them.
//
// HTTP input is the provenance boundary: the name must be a slug (the
// Run Id rule; a name is a URL path segment in DELETE /v1/sigils/{name},
// and a name with a slash could never be erased) and the path must be
// non-empty, nothing more. The path is not stat'ed: checking executables
// is impctl's job at the point a human names one, and here a bad path is
// simply a failed Run, visible where failed Runs are looked for.
package main

import (
	"encoding/json"
	"net/http"
	"sort"

	"imp/lib/go/wire"
)

// handleListSigils is GET /v1/sigils: name-sorted, because a listing that
// shuffled between polls would read as churn.
func (d *daemon) handleListSigils(w http.ResponseWriter, _ *http.Request) {
	d.mu.Lock()
	sigils := []wire.Sigil{} // a bare array even when empty, never null
	for name, path := range d.sigils {
		sigils = append(sigils, wire.Sigil{Name: name, Path: path})
	}
	d.mu.Unlock()
	sort.Slice(sigils, func(i, j int) bool { return sigils[i].Name < sigils[j].Name })
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(sigils)
}

// handleInscribe is POST /v1/sigils: 201 on create, 200 on unchanged or
// replaced, with the outcome named in the body either way.
func (d *daemon) handleInscribe(w http.ResponseWriter, req *http.Request) {
	var body wire.Sigil
	if err := json.NewDecoder(req.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	nameIsSlug := slugPattern.MatchString(body.Name)
	pathIsEmpty := body.Path == ""
	bodyIsInvalid := !nameIsSlug || pathIsEmpty
	if bodyIsInvalid {
		http.Error(w, "sigil needs a slug name (lowercase, digits, hyphens) and a non-empty path", http.StatusBadRequest)
		return
	}

	d.mu.Lock()
	previous, existed := d.sigils[body.Name]
	d.sigils[body.Name] = body.Path
	d.mu.Unlock()

	result := wire.InscribeResult{Name: body.Name, Path: body.Path}
	status := http.StatusOK
	pathUnchanged := existed && previous == body.Path
	pathMoved := existed && previous != body.Path
	switch {
	case pathUnchanged:
		result.Outcome = "unchanged"
	case pathMoved:
		result.Outcome = "replaced"
		result.Previous = &previous
	default:
		result.Outcome = "created"
		status = http.StatusCreated
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(result)
}

// handleErase is DELETE /v1/sigils/{name}: 204 removed, 404 absent. Runs
// launched under the name are untouched; they carry their own path.
func (d *daemon) handleErase(w http.ResponseWriter, req *http.Request) {
	name := req.PathValue("name")
	d.mu.Lock()
	_, present := d.sigils[name]
	delete(d.sigils, name)
	d.mu.Unlock()
	if !present {
		http.Error(w, "no such sigil", http.StatusNotFound)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
