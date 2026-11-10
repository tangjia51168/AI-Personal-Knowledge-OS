import test from "node:test";
import assert from "node:assert/strict";
import {parseCollections, parseCollectionDetail} from "../apps/web/src/lib/collections.ts";
const group = {id:"00000000-0000-0000-0000-000000000001",kind:"folder",name:"资料",active:true,version:1,created_at:"2026-11-10T00:00:00Z",updated_at:"2026-11-10T00:00:00Z"};
test("collection contracts reject duplicate identities, invalid versions and unsafe links", () => {
  assert.deepEqual(parseCollections([group]), [group]);
  assert.throws(() => parseCollections([group, group]));
  assert.throws(() => parseCollections([{...group,version:0}]));
  assert.deepEqual(parseCollectionDetail({collection:group,items:[]}).items, []);
  for (const url of ["javascript:alert(1)","https://user:secret@example.com",""]) assert.throws(() => parseCollectionDetail({collection:group,items:[{source_id:group.id,position:0,url}]}));
  assert.throws(() => parseCollectionDetail({collection:group,items:[{source_id:group.id,position:200,url:"https://example.com"}]}));
});
