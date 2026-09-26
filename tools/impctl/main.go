// impctl: the door to the workspace's Daemon, a thin CLI over lib/go/client.
//
// Every verb is one socket call printed as JSON, one document per line
// (greppable, pipeable, and exactly the Daemon's documents; the CLI adds no
// presentation of its own), except the three lifecycle verbs `up`, `down`,
// and `status`, which manage the Daemon process itself and live in
// lifecycle.go, and `apply`, which loads a Manifest and issues its calls
// (manifest.go). Run it from the workspace root: cwd is how a Client finds
// its workspace's Daemon, and an Imp launched by the Daemon inherits that
// cwd, which is how `impctl watch --once` works bare from inside a Run.
//
// This file is dispatch only. `launch` is the manual door for judgment-call
// relaunches (a failed Run redone under a fresh Run Id); `watch --once` is
// how an Imp arms a follow-on wait mid-Run; `tick` exists for tests and for
// "I edited a Sensor, fire it now". The `--` before a watch argv
// is what stops flag parsing, so a Sensor argument starting with
// `-` is passed through rather than read as impctl's own flag.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"

	"imp/lib/go/client"
	"imp/lib/go/wire"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	verb, args := os.Args[1], os.Args[2:]
	switch verb {
	case "up":
		up(args)
	case "down":
		down()
	case "status":
		status()
	case "apply":
		if len(args) != 1 {
			usage()
		}
		if err := applyManifest(client.New(), args[0]); err != nil {
			log.Fatal(err)
		}
	default:
		socketVerb(verb, args)
	}
}

// socketVerb is every verb that is one client call and a JSON print.
func socketVerb(verb string, args []string) {
	c := client.New()
	switch verb {
	case "sigils":
		sigils, err := c.ListSigils()
		if err != nil {
			log.Fatal(err)
		}
		for _, s := range sigils {
			printJSON(s)
		}
	case "inscribe":
		if len(args) != 2 {
			usage()
		}
		// impctl checks every executable it names; the Daemon never stats.
		if !isExecutableFile(args[1]) {
			log.Fatalf("%q is not an executable file", args[1])
		}
		result, err := c.Inscribe(args[0], args[1])
		if err != nil {
			log.Fatal(err)
		}
		printJSON(result)
	case "erase":
		if len(args) != 1 {
			usage()
		}
		if err := c.Erase(args[0]); err != nil {
			log.Fatal(err)
		}
	case "watches":
		watches, err := c.ListWatches()
		if err != nil {
			log.Fatal(err)
		}
		for _, w := range watches {
			printJSON(w)
		}
	case "watch":
		fs := flag.NewFlagSet("watch", flag.ExitOnError)
		once := fs.Bool("once", false, "drop the Watch after its first emission")
		fs.Parse(args) // "--" ends flags; fs.Args() is the Sensor argv
		argv := fs.Args()
		if len(argv) == 0 {
			usage()
		}
		// impctl checks every executable it names; the Daemon never stats. A
		// Sensor without its exec bit would otherwise fail in the watch
		// log every Tick with nothing else saying so.
		if !isExecutableFile(argv[0]) {
			log.Fatalf("%q is not an executable file", argv[0])
		}
		w, _, err := c.Watch(argv, *once)
		if err != nil {
			log.Fatal(err)
		}
		printJSON(w)
	case "unwatch":
		if len(args) != 1 {
			usage()
		}
		id, err := strconv.Atoi(args[0])
		if err != nil {
			usage()
		}
		if err := c.Unwatch(id); err != nil {
			log.Fatal(err)
		}
	case "runs":
		runs, err := c.ListRuns()
		if err != nil {
			log.Fatal(err)
		}
		for _, r := range runs {
			printJSON(r)
		}
	case "launch":
		if len(args) < 2 {
			usage()
		}
		run, err := c.Launch(wire.Launch{Sigil: args[0], Id: args[1], Args: args[2:]})
		if err != nil {
			log.Fatal(err)
		}
		printJSON(run)
	case "tick":
		summary, err := c.Tick()
		if err != nil {
			log.Fatal(err)
		}
		printJSON(summary)
	default:
		usage()
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `usage:
  impctl up [--manifest PATH]...   start the Daemon if needed, wait for ready, apply each Manifest
  impctl down                      SIGTERM the Daemon and wait for its lock to free
  impctl status                    down | up | starting-or-wedged, then sigils, watches, runs, log tail
  impctl apply PATH                inscribe and watch everything the Manifest declares
  impctl sigils | inscribe NAME PATH | erase NAME
  impctl watches | watch [--once] -- ARGV... | unwatch ID
  impctl runs | launch SIGIL RUN_ID [ARGS...]
  impctl tick
`)
	os.Exit(2)
}

func printJSON(v any) {
	line, err := json.Marshal(v)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(string(line))
}
