# Lantern PXE

A self-hosted PXE/iPXE boot server with a web console — same goal and
user-facing behavior as [Beacon](https://github.com/acebmxer/beacon_pxe)
(web UI, PXE boot menu, image management, DHCP/TFTP/NFS boot flow), but
built around Podman's rootless model instead of Docker's root daemon.

Read [docs/design.md](docs/design.md) before doing anything — it has the
full rationale (why this is a new project, not a Podman port of Beacon)
and the target service topology. Don't ask Nick to re-explain any of that;
it's all there. [docs/design.md](docs/design.md) is the source of truth;
this file is just the entry point to it.

## Status

Design stage. Nothing has been built yet. No code exists in this repo.

## When told to "start" or "go"

That means: begin implementation. Don't ask what the project is or ask
for the background again — start with this, in order:

1. **Scaffold the rootless-safe web layer first.** The FastAPI app
   (auth, users, settings, image handling/ISO processing, boot-menu/iPXE
   generation, SQLite DB, driver staging) doesn't depend on any of the
   open topology questions below and can be built and run standalone,
   in a rootless container, reusing Beacon's app shape per the "What
   carries over from Beacon" section of the design doc.
2. **Stub the parts that depend on unresolved architecture** (DHCP
   config generation/reload, the NFS/HTTP boot-root choice, SMB
   placement) behind clear interfaces rather than blocking on them —
   note them as TODOs tied to the open questions in the design doc.
3. **Surface the open questions from design.md as they become blocking**,
   not all at once up front. If reaching a real fork (e.g. actually
   wiring DHCP reload, or picking the NFS replacement) requires a
   decision, ask about that one decision when you get there — don't
   front-load the whole open-questions list as a precondition to
   starting.

If something in step 1 turns out to already need a topology decision
sooner than expected, say so in one line and ask — don't stall silently
and don't guess on anything hard to undo (on-disk layout, the DB schema,
public interfaces).
