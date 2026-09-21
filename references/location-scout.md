# Location scouting from photos

The user sends photos of a space, optionally with measurements, and wants a
recommendation: which background, which chairs, where to put things, and what the
people on camera should wear.

**Photos support a recommendation. They do not replace measuring on site.** Give the
verdict, then name the two or three numbers that must be measured before the plan is
trusted — see the seat-height trap below, which is the one that silently invalidates
every lighting number.

---

## Read the photo in this order

Do not start with aesthetics. Start with what would ruin a take.

### 1. Things in frame that post cannot cheaply remove

- **Vertical objects on the background**: drape stanchions, pipes, poles, door
  frames, downpipes. A pale vertical line on a dark background lands between or
  behind the subjects and reads as a mistake. Can it be moved, hidden in a fold, or
  framed out?
- **Light leaks**: any glow through a curtain or under a door. On a dark background
  a leak becomes the brightest thing in frame and pulls the eye, and it usually
  arrives at a different colour temperature than the key.
- **Signage, branding, lit banners, screens.** Bright, saturated, and they date the
  footage or tie it to a campaign. Ask whether they can be switched off.
- **Wall furniture**: outlets, thermostats, vents, extinguishers, art, switch
  plates. Cheap to avoid by placement, expensive to paint out.
- **Reflective surfaces** that can see the key: glass, whiteboards, framed art,
  polished metal, TV screens.

### 2. The background's tonal behaviour

| Background | Gives you | Costs you |
|---|---|---|
| Dark drape / dark wall | Instant separation, no background light needed, reads formal | Shows every wrinkle and every light leak; dark clothing merges into it; rear spill from a soft key rakes the folds |
| Light / cream / white wall | Forgiving of dark wardrobe, bounces soft fill back onto faces, and the standard "subject as far off the wall as the key is in front of them ⇒ ~2 stops under" geometry applies | Needs background light set correctly; reveals wall furniture; looks like a corridor if unlit |
| Mid-tone wall | Easiest to balance | Least visually distinctive |

**Deciding question: which one fails more gracefully if it is slightly wrong?** A
light wall lit imperfectly is still usable and gradeable. A dark background with a
visible pole or a colour-cast leak is a reframe or a paint-out.

### 3. Ceiling and overheads

- **Dropped acoustic tile** is good news for the noise floor — it absorbs.
- **Recessed downlights directly above the subjects** put a hot toplight on heads
  and arrive at a different colour temperature (office LED is often ~4000 K).
  Ask whether they can be switched off, and whether motion sensors will defeat that.
- Note projectors, ceiling speakers, and HVAC diffusers — all noise sources.
- **Low ceilings limit how high a key can go**, which interacts with the down-angle
  ceiling below.

### 4. Chairs — a technical choice, not a comfort one

Rank the options visible in the photo:

| Reject | Why |
|---|---|
| Anything that **swivels or rolls** | The subject rotates away from the key and out of the focus plane mid-answer. Unrecoverable when the operator is also on camera. |
| **Deep or reclining** chairs | People lean back. At a long focal length wide open, total depth of field can be a handful of inches — a leaner leaves focus. |
| **Height-adjustable** chairs | The height drifts or gets bumped, invalidating every lighting number. |
| **Mismatched** chairs for two subjects | Reads as hierarchy or carelessness. |
| **Glossy or chrome** frames | Specular hits from the key. |

Prefer: a **matched pair**, fixed legs, upright, shallow seat, low back that does not
rise behind the head, neutral upholstery that will not cast colour onto faces.

### ⚠️ The seat-height trap

Lighting plans are usually written against an assumed seated eye height. **Low
upholstered lounge chairs seat 15–17" versus 18–19" for an occasional chair, so eye
height often lands 42–45" rather than the ~47" that gets assumed.** A soft cushion
compresses further under load.

That shifts the key's down angle by several degrees, and down angle has a hard
ceiling (past roughly 35°, seated subjects get eye-socket shadows). **Recompute,
don't estimate**, and give the corrected fixture height as a literal number:

```
down_angle = atan((lantern_height - eye_height) / horizontal_distance)
lantern_height_for_target = eye_height + distance * tan(target_angle)
```

To raise the angle, move the fixture **further out**, not just higher — tilting more
is what causes the shadows.

---

## Wardrobe guidance

Two constraints are technical and non-negotiable; the rest is taste.

**Technical:**

- **No fine repeating patterns** — tight stripes, small checks, herringbone, tweed.
  They alias into moiré on a high-resolution sensor, worst on a wide lens where the
  pattern is small in frame. This is the single most common avoidable wardrobe fault.
- **No highly saturated red** on a 4:2:0 camera — chroma smears at edges.

**Tonal, driven by the background:**

| Background | Recommend | Avoid |
|---|---|---|
| Dark | Mid-tones and lighter: mid-blue, light grey, soft colours | Black, charcoal, anything that merges. A dark suit on a dark background has no separation and no fix. |
| Light / cream | Mid-to-deep saturated solids: navy, charcoal, deep teal, burgundy, forest | White, cream, pale grey (merges); very light shirts under a bright key draw the eye |

**Always:**

- **Two subjects should not wear the same colour** — they read as a uniform and the
  two-shot flattens.
- **Do not match the upholstery.** If the chairs are taupe, no taupe/beige/camel.
- **Off-white rather than bright white shirts** under a strong key.
- **Skip large polished watch faces and pendants** — a big soft source near the lens
  axis puts a specular hit straight back down it.
- **No logos** unless intentional.

### ⚠️ Glasses and an on-axis key

If the key is soft, large, and sits **near the lens axis** — for example nested
directly above the wide camera — that is the worst possible geometry for eyeglasses:
it reflects as a bright rectangle dead centre in each lens. An operator who is also
on camera cannot see this happening.

Ask in advance whether anyone wears glasses. If so, mitigate **before** rolling:
move the key slightly off-axis, raise it (within the down-angle ceiling), or have
them tilt the temples down a fraction so the reflection falls below the eyeline.
Check it on the monitor with them seated, not from behind the camera.

---

## Coverage: who gets a camera decides what the edit can do

Judge this from the photo, before the day, because it cannot be fixed afterwards.

With a tight angle on each person, the edit cuts on dialogue and looks like an
interview. With a tight angle on the **guest** and only a wide two-shot covering the
host, every host question plays as the same static wide frame — and the usual remedy is
a slow synthetic push-in on the wide, which is always worse than having shot a second
angle.

- **If both people will be quoted, plan a second angle.** A solo operator with two
  bodies usually spends them on tight-guest plus wide; that is the right call when only
  the guest is on screen, and the wrong one when the host is a named participant.
- **A wide that has to double as the host's coverage needs room to be cropped into.**
  Frame it a little looser than you otherwise would and keep the host off the extreme
  edge, so a later push-in has somewhere to go.
- The push itself belongs to post — see `post-production.md` — but the decision belongs
  here, at the photo stage.

---

## Feeding measurements into the diagrams

If the user supplies room dimensions, put them in the diagram generator's `SCENE`
block rather than describing the layout in prose — the geometry checks then run for
free and will catch a plan that does not fit.

Ask for: room width and depth, ceiling height, the distance from the chosen
background to the nearest obstruction, outlet positions, window positions and
orientation, and door swing. **Measured seat height beats all of them** for
lighting purposes.

---

## Keep the client's location out of shared material

Photos of a client site, the client's name, and anything on their walls are theirs.
Analysis of a specific location belongs in that shoot's folder, never in a shared
skill, template, or public repository. Write the *method* back to the durable layer;
leave the location in the shoot folder.
