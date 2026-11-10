import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { currentTrack, moduleResults, progressUpdated, statusNames, trackModules, tracks } from "../apps/web/src/lib/project-progress.ts";
import { accountSections, isAccountSection } from "../apps/web/src/lib/account-sections.ts";
import { referenceRecord, requirementStateNames, trackAcceptance, trackVerification } from "../apps/web/src/lib/track-acceptance.ts";

const root = fileURLToPath(new URL("../", import.meta.url));

test("track deliveries reference existing records without implying overall completion", () => {
  assert.equal(currentTrack.id, "console");
  assert.deepEqual(Object.keys(trackModules).sort(), tracks.map(track => track.id).sort());
  const known = new Set(moduleResults.map(item => item.id));
  for (const ids of Object.values(trackModules)) {
    assert.equal(new Set(ids).size, ids.length);
    assert.ok(ids.every(id => known.has(id)));
  }
  assert.match(currentTrack.remaining, /异地/);
  assert.equal(tracks.find(track => track.id === "foundation").status, "partial");
});

test("progress IDs, statuses and dates are bounded and unique", () => {
  assert.match(progressUpdated, /^\d{4}-\d{2}-\d{2}$/);
  for (const records of [tracks, moduleResults]) {
    assert.ok(records.length > 0 && records.length <= 50);
    assert.equal(new Set(records.map(record => record.id)).size, records.length);
    for (const record of records) assert.match(record.id, /^[a-z][a-z-]{1,50}$/);
  }
  for (const track of tracks) {
    assert.ok(Object.hasOwn(statusNames, track.status));
    assert.ok(track.result.length > 0 && track.remaining.length > 0);
  }
});

test("module cards point only to explicit account sections and existing repository documents", () => {
  for (const item of moduleResults) {
    assert.ok(accountSections.includes(item.target));
    assert.match(item.document, /^(docs\/[a-z][a-z0-9-]*\.md|ARCHITECTURE\.md)$/);
    assert.ok(existsSync(root + item.document));
    for (const key of ["checks", "baseline", "steps", "limitation", "result"]) assert.ok(item[key].length > 0);
    assert.match(item.date, /^\d{4}-\d{2}-\d{2}$/);
  }
});

test("known account navigation is not a recovery token or an arbitrary fragment", () => {
  for (const section of accountSections) assert.equal(isAccountSection(`#${section}`), true);
  for (const value of ["", "#unknown", "#knowledge?verify=x", "#verify=", "#verify=" + "a".repeat(43), "#reset=" + "b".repeat(43), "#knowledge&reset=x"]) assert.equal(isAccountSection(value), false);
});

test("curated product records are not completed by existing infrastructure or cloud evidence", () => {
  assert.equal(tracks.find(track => track.id === "evidence").status, "partial");
  assert.equal(tracks.find(track => track.id === "rag").status, "partial");
  assert.equal(tracks.find(track => track.id === "learning").status, "partial");
  assert.equal(tracks.find(track => track.id === "local-ai").status, "partial");
  assert.equal(tracks.find(track => track.id === "release").status, "partial");
  assert.match(moduleResults.find(item => item.id === "local-graph").limitation, /不是完整知识图谱/);
  assert.match(moduleResults.find(item => item.id === "note-export").limitation, /不是完整备份/);
});

test("every project track has traceable requirements, gaps, dependencies and existing evidence references", () => {
  assert.deepEqual(trackAcceptance.map(item => item.track).sort(), tracks.map(item => item.id).sort());
  assert.deepEqual(Object.keys(trackVerification).sort(), tracks.map(item => item.id).sort());
  assert.equal(new Set(trackAcceptance.map(item => item.track)).size, tracks.length);
  const knownModules = new Set(moduleResults.map(item => item.id));
  const ids = new Set();
  for (const item of trackAcceptance) {
    assert.ok(Object.values(trackVerification[item.track]).every(value => value.length > 5));
    assert.ok(existsSync(root + item.document));
    assert.ok(item.requirements.length >= 2);
    assert.ok(item.dependencies.every(id => tracks.some(t => t.id === id) && id !== item.track));
    for (const requirement of item.requirements) {
      assert.equal(ids.has(requirement.id), false);
      ids.add(requirement.id);
      assert.ok(Object.hasOwn(requirementStateNames, requirement.state));
      assert.ok(requirement.acceptance.length > 10 && requirement.gap.length > 10);
      assert.ok(requirement.paragraphs[0] >= 0 && requirement.paragraphs[1] < referenceRecord.paragraphs && requirement.paragraphs[0] <= requirement.paragraphs[1]);
      assert.ok(requirement.modules.every(id => knownModules.has(id)));
      if (requirement.state === "recorded") assert.ok(requirement.modules.length > 0);
    }
    // Pending requirements prevent an entire track from being marked delivered.
    if (item.requirements.some(r => r.state !== "recorded")) assert.notEqual(tracks.find(t => t.id === item.track).status, "delivered");
  }
  assert.match(referenceRecord.sha256, /^[a-f0-9]{64}$/);
});

test("progress records and page contain no log readers, dynamic endpoints or tenant API calls", () => {
  const source = readFileSync(root + "apps/web/src/app/progress/page.tsx", "utf8") + readFileSync(root + "apps/web/src/lib/project-progress.ts", "utf8");
  assert.doesNotMatch(source, /fetch\(|process\.env|node:fs|\/api\/account|dangerouslySetInnerHTML|https?:\/\//);
  assert.doesNotMatch(JSON.stringify(moduleResults), /Bearer |SECRET|PASSWORD|\.env|\.cache\//);
});
