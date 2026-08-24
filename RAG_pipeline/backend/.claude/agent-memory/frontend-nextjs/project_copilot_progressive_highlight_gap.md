---
name: copilot-progressive-highlight-gap
description: Progressive canvas highlighting is now wired end-to-end; the remaining known gap is that landscape/integration-level targets have no highlight channel
metadata:
  type: project
---

Both seams that used to block the page-embedded copilot's progressive highlight are closed:
the `tool.completed` public-event allowlist carries `targets`, and `StreamState` exposes the
turn's parsed events. Do not go looking for either as the cause of "highlights never fire".

**Why:** this memory previously recorded them as open blockers discovered under wave-2 file
ownership. Acting on that now would send a session chasing a fixed problem.

**How to apply:** when a copilot highlight does not appear, the live causes are, in order:
(1) the target's level has no emphasis channel on the design canvas -- `project` and
`integration` levels deliberately contribute none, because `LandscapeCanvas` owns its own
selection state and takes no highlight input, so landscape-level answers give clickable chips
but no lit nodes; (2) the target resolved against filtered graph data and came back
unresolvable; (3) a higher-precedence emphasis source owns the canvas.

That precedence is one shared channel (node `data.dimmed`/`data.highlighted` + edge opacity),
not parallel layers, and the order is deliberate: search > canvas hover > field lineage >
copilot > failure-focus resting dim. The copilot tier sits directly above the resting dim so an
answer is visible by default while still yielding to whatever the user is actively doing. A
lower tier restores itself by taking the tiers above it as effect dependencies -- that is what
makes "dismissing an answer restores the prior emphasis" work without a teardown path.

Hard rule on that tier: no `fitView`/`fitBounds`/`setViewport`/`zoomTo`/`setCenter`. It fires
mid-stream from tool results, and moving the viewport while prose is still arriving is
disorienting. Only an explicit chip click navigates. See [[copilot-target-id-spaces]].
