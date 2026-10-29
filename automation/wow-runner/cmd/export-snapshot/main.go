// export-snapshot converts one validated Lua snapshot to JSONL gzip.
package main

import (
	"flag"
	"fmt"
	"os"

	"wow-auction/automation/wow-runner/internal/snapshot"
)

func main() {
	source := flag.String("source", "", "input snapshot.lua")
	output := flag.String("output", "", "new output snapshot.jsonl.gz")
	flag.Parse()
	if *source == "" || *output == "" {
		fmt.Fprintln(os.Stderr, "source and output required")
		os.Exit(2)
	}
	if e := snapshot.ExportJSONL(*source, *output); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
	fmt.Println("snapshot export complete")
}
