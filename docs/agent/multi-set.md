# Alternate builds from the same parts

Use this when the user wants two or more models made from one set of parts
(for example a speaker that rebuilds into a stage, or a 3-in-1 set). Every
build must use the same box: the same part numbers, in the same colours and
the same quantities.

## Words

- **Box**: the parts every build shares. Usually it is build 1's parts. It can
  also be a parts list the user already owns (CSV with `part,colour,quantity`
  columns, a `bom` report, or a saved `parts-box` file).
- **Leftovers**: box parts a build does not use.
- **Missing**: parts a build needs that the box does not have.
- **Hidden filler**: real parts placed out of sight so a build can use the
  whole box. Put them in their own section whose FILE name contains `hidden`,
  for example `stage-hidden-filler.ldr`. The tools count everything in such a
  section as hidden.

## Workflow

1. **Design build 1 normally** and finish its review (`review`, pictures,
   repairs). Its parts list is now the box. Save it so the target is fixed:

   ```sh
   ./ldraw-agent parts-box output/speaker/speaker.mpd --output output/speaker/box.json
   ```

2. **Plan build 2 from the box.** Read `box.json` before choosing parts. Group
   it into structure (bricks, plates), surfaces (tiles, slopes) and special
   parts, and decide where each special part goes in build 2 first. Prefer
   parts already in the box over new ones. Do not add parts outside the box.

3. **Check while you build.** Run `parts-match` on the plan or MPD at each
   stage. Its `leftovers` list is what is still left in the box, and `missing`
   is what you used that the box does not have:

   ```sh
   ./ldraw-agent parts-match output/stage/stage.plan.json --box output/speaker/box.json \
     --report output/stage/parts-match.json
   ```

4. **Close the gap.** Swap missing parts for leftovers of a similar size and
   colour. When a few parts still do not fit:
   - leftovers can go **inside build 2** as hidden filler (in the core, under
     floor tiles, inside walls, under the base), or
   - parts that only build 2 needs can go **inside build 1** as hidden filler.
     Then rebuild build 1, re-run its review and save the box again.

   Hidden filler must be properly attached (normal geometry and connection
   checks still apply) and must not show in the review pictures. Keep it to at
   most 10% of each build (`--hidden-percent`, default 10). Never hide parts
   just to avoid designing.

5. **Finish with a passing match** for every build against the final box,
   then review build 2 as normally (`review`, pictures, repairs):

   ```sh
   ./ldraw-agent parts-match output/stage/stage.mpd --box output/speaker/speaker.mpd
   ```

   It exits 0 when nothing is missing, the leftovers are within
   `--leftovers N` (default 0) and hidden filler is within the limit.
   `--strict` means an exact match with no hidden filler. If the user accepts
   a few leftovers, pass `--leftovers N` and say how many.

6. **Write the Blender pairing file** when there are two builds. It links each
   physical brick of build 1 to the brick it becomes in build 2, for the Better
   Destruction add-on:

   ```sh
   ./ldraw-agent pair-file output/speaker/speaker.mpd output/stage/stage.mpd \
     --output output/speaker-stage.bdpair.json --name1 Speaker --name2 Stage
   ```

   Put the pairing file in a folder that is a parent of, or the same as, both
   model files, so its `file` paths stay relative. For three or more builds,
   write one pairing file for each pair the user wants to animate (1 to 2,
   1 to 3, ...).

## Delivery

Give the user both (or all) `.mpd` files, the box, each `parts-match` report,
the pairing file, and the pictures of every build. Say whether the match is
exact, how many leftovers there are, and how many hidden filler parts each
build has and where they are.

## Pairing file format (`.bdpair.json`)

`format: "bd-pair"`, `version: 1`, `units: "ldu"`, `up: "-Y"`, the two `sets`
(name and file), and one entry in `bricks` for each physical brick:

- `id`: unique, the same brick in both sets.
- `part` (lower case, no `.dat`, variant letters kept) and `color` (LDraw code).
- `set1` / `set2`: `pos` and `rot` (the 9 matrix values) of that brick in the
  set's top-level space, with sub-model placements applied; `center`, the
  middle of the brick's bounding box in the same space; and `hidden`. A set
  the brick is not in is `null`.
- `order`: set 2 build step, bottom-up from 0 (a brick never comes before the
  bricks under it).
- `hidden`: true when the brick is hidden filler in set 2.

Bricks of the same part and colour are paired so they travel the shortest
total distance between the two builds. The tool checks the result before
writing: unique ids, and each side lists exactly its model's parts.
