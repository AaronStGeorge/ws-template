# ws-template

> The trouble with having an open mind, of course, is that people will insist on coming along and trying to put things in it.
>
>   –Terry Pratchett

This repo holds an agent workspace template.
To get started with this workspace just clone with whatever name you like`git clone git@github.com:AaronStGeorge/ws-template.git <project>-ws`, then clone what you're working on into `sources`, and you're off to the races!
Except rather than horses, agents are zipping around — consuming tokens, farting carbon, and hopefully doing something useful.

_How_ the agents are orchestrated and go about actually accomplishing things has been changing pretty frequently.
You'll need to look in `.agents/skills` to get a handle on how things work at the moment.

What goes into `sources/` is a *sourceset*, `sourcesets/<name>/sourceset.json`; `python scripts/ws_load.py <name>` fills the main checkout's `sources/` by hand.
A piece of work gets a *thread*: `thread new <bead-id>` (or `thread new --name <slug> --sourceset <name>`) cuts a worktree of this clone under `.threads/`, loads the sourceset into it, and opens a Codex session there that shows in Codex's agent command center beside everything else running; `thread new ... --prompt TEXT` starts one without attaching.
Imps run their agents the same way.
[lib/python/threadlib/README.md](lib/python/threadlib/README.md) is the record of how and why.

## Setup

`direnv` will do it all for you, alternatively:
```bash
python3 -m venv .venv --prompt ${PWD##*/} && source .venv/bin/activate
pip install -e lib/python --config-settings editable_mode=compat
```

`editable_mode=compat` makes the install a plain path entry rather than a PEP
660 import hook, static analyzers (Pylance/pyright in VS Code) have an issue
without that for some unknown reason.
