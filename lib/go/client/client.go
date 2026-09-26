// Package client is the transport for talking to the Daemon: it dials the
// workspace's unix socket, sends one HTTP request per verb, and turns the
// Daemon's statuses into Go errors. It defines no documents. The types that
// cross the socket and the .imp/ paths live in lib/go/wire, which impd
// imports as well, so both ends of the socket compile against one
// definition of every type and cannot drift.
//
// impctl is this package's only importer. It is a package rather than a
// file inside impctl so that a second Go program talking to the Daemon
// would import it instead of copying it.
//
// A Client finds its Daemon through the current working directory: every
// imp process runs from its workspace root, and with one Daemon per
// workspace, cwd is the whole discovery mechanism. There is no global home.
//
// Two Daemon statuses are outcomes rather than failures and surface as
// sentinel errors. 409 is ErrDuplicateRun: the benign result of re-firing a
// condition under a deterministic Run Id. 404 is ErrNotFound: erasing or
// unwatching what is already gone. Every other status outside the accepted
// set becomes an error carrying the Daemon's own message.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"

	"imp/lib/go/wire"
)

// ErrDuplicateRun reports the Daemon's 409: the Run Id is already occupied.
// Callers relaying re-fired conditions treat this as benign.
var ErrDuplicateRun = errors.New("run id already occupied")

// ErrNotFound reports the Daemon's 404: no such Sigil, Watch, or Run.
var ErrNotFound = errors.New("not found")

// Client talks to the workspace's Daemon over its unix socket.
type Client struct {
	http *http.Client
}

// New returns a Client dialing wire.SocketPath relative to the current
// working directory, the workspace root where every imp command runs.
func New() *Client {
	transport := &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var dialer net.Dialer
			return dialer.DialContext(ctx, "unix", wire.SocketPath)
		},
	}
	return &Client{http: &http.Client{Transport: transport}}
}

// The host in these URLs is a placeholder; the transport always dials the socket.
const baseURL = "http://impd/v1"

// ListSigils returns every Sigil, name-sorted by the Daemon.
func (c *Client) ListSigils() ([]wire.Sigil, error) {
	var sigils []wire.Sigil
	_, err := c.do(http.MethodGet, "/sigils", nil, &sigils, http.StatusOK)
	return sigils, err
}

// Inscribe binds name to path. The result's Outcome says whether the Sigil
// was created, left unchanged, or moved from a previous path.
func (c *Client) Inscribe(name, path string) (wire.InscribeResult, error) {
	var result wire.InscribeResult
	_, err := c.do(http.MethodPost, "/sigils", wire.Sigil{Name: name, Path: path}, &result, http.StatusCreated, http.StatusOK)
	return result, err
}

// Erase removes a Sigil; ErrNotFound when there is none by that name.
func (c *Client) Erase(name string) error {
	_, err := c.do(http.MethodDelete, "/sigils/"+name, nil, nil, http.StatusNoContent)
	return err
}

// ListWatches returns every Watch, id-sorted by the Daemon.
func (c *Client) ListWatches() ([]wire.Watch, error) {
	var watches []wire.Watch
	_, err := c.do(http.MethodGet, "/watches", nil, &watches, http.StatusOK)
	return watches, err
}

// Watch adds a Sensor argv. An argv already watched with the same
// once flag comes back as the existing Watch with created false; that
// idempotence is what makes re-applying a Manifest free.
func (c *Client) Watch(argv []string, once bool) (wire.Watch, bool, error) {
	var watch wire.Watch
	status, err := c.do(http.MethodPost, "/watches", wire.Watch{Argv: argv, Once: once}, &watch, http.StatusCreated, http.StatusOK)
	created := status == http.StatusCreated
	return watch, created, err
}

// Unwatch removes a Watch by id; ErrNotFound when there is none.
func (c *Client) Unwatch(id int) error {
	_, err := c.do(http.MethodDelete, "/watches/"+strconv.Itoa(id), nil, nil, http.StatusNoContent)
	return err
}

// Tick asks the Daemon for one pass over every Watch and returns once the
// pass is done.
func (c *Client) Tick() (wire.TickSummary, error) {
	var summary wire.TickSummary
	_, err := c.do(http.MethodPost, "/tick", nil, &summary, http.StatusOK)
	return summary, err
}

// Launch POSTs a Launch and returns the Run as it stands on return.
func (c *Client) Launch(body wire.Launch) (wire.Run, error) {
	var run wire.Run
	_, err := c.do(http.MethodPost, "/runs", body, &run, http.StatusAccepted)
	return run, err
}

// ListRuns returns every Run the Daemon holds.
func (c *Client) ListRuns() ([]wire.Run, error) {
	var runs []wire.Run
	_, err := c.do(http.MethodGet, "/runs", nil, &runs, http.StatusOK)
	return runs, err
}

// GetRun returns the Run under one Run Id; ErrNotFound when there is none.
func (c *Client) GetRun(id string) (wire.Run, error) {
	var run wire.Run
	_, err := c.do(http.MethodGet, "/runs/"+id, nil, &run, http.StatusOK)
	return run, err
}

// do is the one request path: encode in (when non-nil) as the JSON body,
// decode the response into out (when non-nil), and return the status code
// so callers that distinguish 201 from 200 can. A status outside accepted
// is an error: the two sentinel statuses map to their sentinel errors, and
// anything else carries the Daemon's own message, which is the diagnostic.
func (c *Client) do(method, path string, in, out any, accepted ...int) (int, error) {
	var payload io.Reader
	if in != nil {
		encoded, err := json.Marshal(in)
		if err != nil {
			return 0, err
		}
		payload = bytes.NewReader(encoded)
	}
	req, err := http.NewRequest(method, baseURL+path, payload)
	if err != nil {
		return 0, err
	}
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()

	statusAccepted := false
	for _, status := range accepted {
		if resp.StatusCode == status {
			statusAccepted = true
		}
	}
	if !statusAccepted {
		switch resp.StatusCode {
		case http.StatusConflict:
			return resp.StatusCode, ErrDuplicateRun
		case http.StatusNotFound:
			return resp.StatusCode, ErrNotFound
		}
		message, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, fmt.Errorf("%s %s: %s: %s", method, path, resp.Status, strings.TrimSpace(string(message)))
	}
	if out == nil {
		return resp.StatusCode, nil
	}
	return resp.StatusCode, json.NewDecoder(resp.Body).Decode(out)
}
