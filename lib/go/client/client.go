// Package client is the single Go implementation of the Daemon's control
// API — Launch, ListRuns, GetRun, ListImps over the workspace's
// `.imp/daemon.sock`; impwatch and the impctl CLI both ride it so the two
// cannot drift.
//
// Every imp process runs from its workspace root and finds that workspace's
// Daemon through the current working directory — with one Daemon per
// workspace, cwd IS the discovery mechanism, and there is no global home.
// The Daemon's 409 surfaces distinctly as ErrDuplicateRun so a caller
// relaying a re-fired condition can treat the duplicate as the benign,
// expected outcome it is rather than an error.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"path/filepath"
)

// LaunchBody is the document that launches a Run (Daemon POST /v1/runs).
type LaunchBody struct {
	Imp  string   `json:"imp"`
	Id   string   `json:"id"`
	Args []string `json:"args"`
}

// Run mirrors the Daemon's Run document.
type Run struct {
	Imp   string  `json:"imp"`
	Id    string  `json:"id"`
	State string  `json:"state"`
	Error *string `json:"error"`
}

// Imp mirrors one entry of the Daemon's configured imp map,
// as GET /v1/imps reports it.
type Imp struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

// ErrDuplicateRun reports the Daemon's 409: the Run Id is already occupied.
// Callers arming idempotent conditions treat this as benign.
var ErrDuplicateRun = errors.New("run id already occupied")

// Client talks to the workspace's Daemon over its unix socket.
type Client struct {
	http *http.Client
}

// New returns a Client dialing .imp/daemon.sock relative to the current
// working directory — the workspace root, where every imp command runs.
func New() *Client {
	socketPath := filepath.Join(".imp", "daemon.sock")
	transport := &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var dialer net.Dialer
			return dialer.DialContext(ctx, "unix", socketPath)
		},
	}
	return &Client{http: &http.Client{Transport: transport}}
}

// The host in these URLs is a placeholder; the transport always dials the socket.
const baseURL = "http://impd/v1"

// Launch POSTs a Launch Body and returns the initial Run document.
func (c *Client) Launch(body LaunchBody) (Run, error) {
	payload, err := json.Marshal(body)
	if err != nil {
		return Run{}, err
	}
	resp, err := c.http.Post(baseURL+"/runs", "application/json", bytes.NewReader(payload))
	if err != nil {
		return Run{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusConflict {
		return Run{}, ErrDuplicateRun
	}
	if resp.StatusCode != http.StatusAccepted {
		return Run{}, fmt.Errorf("launch: unexpected status %s", resp.Status)
	}
	var run Run
	err = json.NewDecoder(resp.Body).Decode(&run)
	return run, err
}

// ListRuns returns every Run the Daemon holds.
func (c *Client) ListRuns() ([]Run, error) {
	resp, err := c.http.Get(baseURL + "/runs")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("list runs: unexpected status %s", resp.Status)
	}
	var runs []Run
	err = json.NewDecoder(resp.Body).Decode(&runs)
	return runs, err
}

// GetRun returns the Run under one Run Id.
func (c *Client) GetRun(id string) (Run, error) {
	resp, err := c.http.Get(baseURL + "/runs/" + id)
	if err != nil {
		return Run{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Run{}, fmt.Errorf("get run: unexpected status %s", resp.Status)
	}
	var run Run
	err = json.NewDecoder(resp.Body).Decode(&run)
	return run, err
}

// ListImps returns the Daemon's configured imp map — a read-only
// view of what its config file loaded.
func (c *Client) ListImps() ([]Imp, error) {
	resp, err := c.http.Get(baseURL + "/imps")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("list imps: unexpected status %s", resp.Status)
	}
	var regs []Imp
	err = json.NewDecoder(resp.Body).Decode(&regs)
	return regs, err
}
