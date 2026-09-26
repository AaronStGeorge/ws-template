// Package wire is everything impd and impctl must agree on and nothing
// else: the JSON documents that cross the Daemon's socket, and the paths
// under .imp/ that both binaries read. It has one definition of each so
// the two cannot drift; a field added here is a field on both ends of the
// socket in the same build, which is the only way "the CLI prints exactly
// the Daemon's documents" can be true by construction rather than by two
// struct declarations happening to match.
//
// Nothing here has behavior. The Daemon (tools/impd) owns the semantics of
// every document and the client library (lib/go/client) owns the
// transport; this package is the contract between them, kept apart from
// both so neither imports the other.
package wire

// The Daemon's files under the workspace root. LockPath is the
// single-instance flock, and its contents are the holder's pid: one file,
// one writer, so there is never a "lock held but no pid" state.
const (
	ImpDir       = ".imp"
	LockPath     = ".imp/daemon.lock"
	SocketPath   = ".imp/daemon.sock"
	WatchLogPath = ".imp/watch.log"
)

// Sigil is one name-to-executable binding: what GET /v1/sigils lists and
// what POST /v1/sigils receives.
type Sigil struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

// InscribeResult is POST /v1/sigils' answer: the binding as it now stands,
// what happened ("created", "unchanged", or "replaced"), and the path
// displaced, nil unless replaced.
type InscribeResult struct {
	Name     string  `json:"name"`
	Path     string  `json:"path"`
	Outcome  string  `json:"outcome"`
	Previous *string `json:"previous"`
}

// Watch is one Sensor argv the Daemon runs every Tick, with the
// id the Daemon assigned and the one-shot flag. POST /v1/watches receives
// it without the id.
type Watch struct {
	Id   int      `json:"id"`
	Argv []string `json:"argv"`
	Once bool     `json:"once"`
}

// Run is the Daemon's Run document, which is also its in-memory record:
// keeping them one type is what guarantees the API cannot drift from the
// state it reports. State moves starting -> running -> succeeded|failed;
// Path is the executable actually exec'd, captured at launch and never
// rewritten.
type Run struct {
	Sigil string  `json:"sigil"`
	Id    string  `json:"id"`
	Path  string  `json:"path"`
	State string  `json:"state"`
	Error *string `json:"error"`
}

// Launch is the JSON that asks for a Run: what a Client POSTs to /v1/runs
// and what a Sensor emits on stdout, one per line.
type Launch struct {
	Sigil string   `json:"sigil"`
	Id    string   `json:"id"`
	Args  []string `json:"args"`
}

// TickSummary is what POST /v1/tick returns once the pass is done.
type TickSummary struct {
	Watches    int `json:"watches"`
	Launched   int `json:"launched"`
	Duplicates int `json:"duplicates"`
	Dropped    int `json:"dropped"`
}
