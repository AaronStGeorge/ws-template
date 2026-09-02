// impctl: the door to the workspace's Daemon — client library plus thin CLI.
//
// This file holds the CLI entry point (see tools/README.md for the
// boundary): `imps` lists the Daemon's configured imp map, `runs`
// lists Runs, `launch` POSTs a Launch Body. All are veneers over
// lib/go/client, which impwatch also uses; keep the CLI thin so the library
// stays the single implementation of the Daemon's API. Run it from the
// workspace root — cwd is how a Client finds its workspace's Daemon.
// Resist growing generality here — the original impctl died of imagined
// generality before a line was written.
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"

	"imp/lib/go/client"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	c := client.New()
	switch os.Args[1] {
	case "imps":
		regs, err := c.ListImps()
		if err != nil {
			log.Fatal(err)
		}
		for _, reg := range regs {
			printJSON(reg)
		}
	case "runs":
		runs, err := c.ListRuns()
		if err != nil {
			log.Fatal(err)
		}
		// One JSON document per line: greppable, pipeable, and exactly the
		// Daemon's Run documents — the CLI adds no presentation of its own.
		for _, r := range runs {
			printJSON(r)
		}
	case "launch":
		// The manual door: judgment-call relaunches (a failed Run redone
		// under a fresh Run Id) come through here rather than hand-written
		// POSTs.
		if len(os.Args) < 4 {
			usage()
		}
		imp, id, args := os.Args[2], os.Args[3], os.Args[4:]
		run, err := c.Launch(client.LaunchBody{Imp: imp, Id: id, Args: args})
		if err != nil {
			log.Fatal(err)
		}
		printJSON(run)
	default:
		usage()
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: impctl imps | impctl runs | impctl launch PIPELINE RUN_ID [ARGS...]")
	os.Exit(2)
}

func printJSON(v any) {
	line, err := json.Marshal(v)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(string(line))
}
