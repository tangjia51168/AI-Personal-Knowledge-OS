import assert from "node:assert/strict";
import test from "node:test";
import { possibleTopicParents, topicPath } from "../apps/web/src/lib/topic-tree.ts";
const root = { id: "a", kind: "topic", name: "基础", active: true, parent_id: null };
const child = { ...root, id: "b", name: "电路", parent_id: "a" };
const leaf = { ...root, id: "c", name: "实践", parent_id: "b" };

test("topic paths preserve archived ancestors, literal names and reject cycles or broken chains", () => {
  assert.equal(topicPath([root, child, leaf], "c"), "基础 / 电路 / 实践");
  assert.equal(topicPath([{ ...root, active: false }, child], "b"), "基础（已归档） / 电路");
  assert.throws(() => topicPath([child], "b"));
  assert.throws(() => topicPath([{ ...root, parent_id: "b" }, child], "b"));
  assert.throws(() => topicPath([root, root], "a"));
  assert.throws(() => topicPath([root, { ...child, kind: "tag" }], "b"));
  const chain = Array.from({ length: 100 }, (_, i) => ({ ...root, id: String(i), parent_id: i ? String(i - 1) : null }));
  assert.equal(topicPath(chain, "99").split(" / ").length, 100);
  assert.throws(() => topicPath([...chain, { ...root, id: "100", parent_id: "99" }], "100"));
});

test("parent options exclude self, descendants, tags and archived targets", () => {
  const other = { ...root, id: "d", name: "其他" };
  const labels = [root, child, leaf, other, { ...root, id: "e", kind: "tag" }, { ...root, id: "f", active: false }];
  assert.deepEqual(possibleTopicParents(labels, "a").map(item => item.id), ["d"]);
  assert.deepEqual(possibleTopicParents(labels, "b").map(item => item.id), ["a", "d"]);
  assert.deepEqual(possibleTopicParents([child], "a"), []);
  assert.deepEqual(possibleTopicParents([{ ...root, parent_id: "b" }, child], "d"), []);
});
