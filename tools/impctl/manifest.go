// The Manifest: a checked-in JSON file declaring one loop's Sigils and
// standing Watches, and `apply`, the loop over two client calls that
// issues them.
//
// The Daemon never reads a Manifest; this file turns one into the
// imperative calls the Daemon does understand, so the Daemon stays
// ignorant of files. `apply` is additive: it ensures each declared Sigil
// and Watch exists and removes nothing, so a Sigil or Watch added by hand
// survives the next `up`. The Daemon's idempotence (inscribe of the same
// path is `unchanged`, watch of the same argv is `existing`) is what makes
// re-applying free, and the per-line output names which case each entry
// hit, so a `replaced` after an unexpected edit is visible.
//
// The Manifest file is the provenance boundary for its contents: a human
// edits it, so unknown keys, non-slug names, empty paths, empty argvs, and
// Sigil paths or Sensors that are not executable files are rejected
// here with the file named, before anything reaches the Daemon. The rule
// for executables is simple: impctl checks every one it names, here and in
// `inscribe` and `watch`, and the Daemon never stats a path. The check
// lives on the Client side because that is where a human is looking when
// they mistype a path or forget an exec bit; left to the Daemon, an Imp's
// mistake is a failed Run and a Sensor's is a watch-log line every
// Tick with nothing else saying so.
//
// The slug rule for names is the Daemon's (a name is a URL path
// segment); it is repeated here only because this is a different process's
// boundary, not a second opinion.
// Paths and argv are sent verbatim; they resolve from the workspace root,
// which is the Daemon's cwd.
//
// One consequence, documented in scripts/imps/README.md: editing a
// standing Watch's argv in a Manifest and re-running `up` adds a second
// Watch beside the old one, because the Daemon matches on exact argv. The
// old one is removed with `impctl unwatch ID`, or by `down` then `up`.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"

	"imp/lib/go/client"
)

// slugPattern is the Sigil name rule: lowercase, digits, hyphen-joined.
var slugPattern = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

// manifest is the file's shape: Sigil name to workspace-relative path,
// and standing Watch argvs.
type manifest struct {
	Sigils  map[string]string `json:"sigils"`
	Watches [][]string        `json:"watches"`
}

// loadManifest reads and validates one Manifest file.
func loadManifest(path string) (manifest, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return manifest{}, err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var m manifest
	if err := decoder.Decode(&m); err != nil {
		return manifest{}, fmt.Errorf("%s: %w", path, err)
	}
	for name, sigilPath := range m.Sigils {
		nameIsSlug := slugPattern.MatchString(name)
		pathIsEmpty := sigilPath == ""
		entryIsInvalid := !nameIsSlug || pathIsEmpty
		if entryIsInvalid {
			return manifest{}, fmt.Errorf("%s: sigil %q -> %q: name must be a slug (lowercase, digits, hyphens) and path non-empty", path, name, sigilPath)
		}
		if !isExecutableFile(sigilPath) {
			return manifest{}, fmt.Errorf("%s: sigil %q -> %q: not an executable file", path, name, sigilPath)
		}
	}
	for i, argv := range m.Watches {
		argvIsEmpty := len(argv) == 0
		executableIsEmpty := !argvIsEmpty && argv[0] == ""
		watchIsUnrunnable := argvIsEmpty || executableIsEmpty
		if watchIsUnrunnable {
			return manifest{}, fmt.Errorf("%s: watches[%d]: argv must name an executable", path, i)
		}
		if !isExecutableFile(argv[0]) {
			return manifest{}, fmt.Errorf("%s: watches[%d]: %q is not an executable file", path, i, argv[0])
		}
	}
	return m, nil
}

// isExecutableFile reports whether path names a regular file with an exec
// bit set; `apply`, `inscribe`, and `watch` all use it. Relative paths resolve from the
// cwd, which is the workspace root for impctl exactly as it is for the
// Daemon that will exec them.
func isExecutableFile(path string) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	isRegular := info.Mode().IsRegular()
	hasExecBit := info.Mode().Perm()&0o111 != 0
	return isRegular && hasExecBit
}

// applyManifest inscribes every Sigil (name-sorted, so output is stable)
// and then watches every standing argv, printing what each call found.
func applyManifest(c *client.Client, path string) error {
	m, err := loadManifest(path)
	if err != nil {
		return err
	}
	names := make([]string, 0, len(m.Sigils))
	for name := range m.Sigils {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		result, err := c.Inscribe(name, m.Sigils[name])
		if err != nil {
			return fmt.Errorf("inscribe %s: %w", name, err)
		}
		fmt.Printf("%s %s -> %s\n", result.Outcome, name, result.Path)
	}
	for _, argv := range m.Watches {
		w, created, err := c.Watch(argv, false)
		if err != nil {
			return fmt.Errorf("watch %s: %w", strings.Join(argv, " "), err)
		}
		outcome := "existing"
		if created {
			outcome = "created"
		}
		fmt.Printf("watch %d standing (%s): %s\n", w.Id, outcome, strings.Join(argv, " "))
	}
	return nil
}
