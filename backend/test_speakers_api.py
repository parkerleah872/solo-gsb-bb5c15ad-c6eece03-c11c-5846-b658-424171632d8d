"""End-to-end API tests for the speaker normalization workbench.

Run with: DATA_DIR=/tmp/x python -m pytest backend/test_speakers_api.py -q
(or directly: python test_speakers_api.py).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="speakers-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def run():
    with TestClient(app) as c:
        p = c.post("/api/projects", json={"name": "归一测试"}).json()
        pid = p["id"]
        doc = {
            "segments": [
                {"id": "s1", "start": 0, "end": 3, "speaker": "小李",
                 "text": "嗯"},
                {"id": "s2", "start": 3, "end": 6, "speaker": "李明",
                 "text": "改制"},
                {"id": "s3", "start": 6, "end": 9, "speaker": "王师傅",
                 "text": "我在车间"},
                {"id": "s4", "start": 9, "end": 10, "speaker": "",
                 "text": "（无说话人）"},
            ],
            "annotations": [
                {"id": "ann-1", "start": 0, "end": 3, "speaker": "小李",
                 "topic": "开场白", "note": "x", "color": "#fff"},
                {"id": "ann-2", "start": 6, "end": 9, "speaker": "",
                 "topic": "未填说话人", "note": ""},
            ],
        }
        c.put(f"/api/projects/{pid}",
              json={"name": "归一测试", "document": doc})
        r1 = c.post(f"/api/projects/{pid}/rules", json={
            "name": "小李的话", "speaker": "小李", "pattern": "嗯",
            "matchType": "keyword",
        }).json()
        r2 = c.post(f"/api/projects/{pid}/rules", json={
            "name": "不限说话人", "speaker": "", "pattern": "改制",
            "matchType": "keyword",
        }).json()

        # ---- preview aggregates names from all three sources ----
        pv = c.post(f"/api/projects/{pid}/speaker-normalization/preview",
                    json={"mappings": []}).json()
        by_name = {n["name"]: n for n in pv["names"]}
        assert set(by_name) == {"小李", "李明", "王师傅"}, by_name
        assert by_name["小李"]["segments"] == 1
        assert by_name["小李"]["annotations"] == 1
        assert by_name["小李"]["rules"] == 1
        assert by_name["李明"]["rules"] == 0
        assert pv["stats"]["nameCount"] == 3
        assert all(not n["changed"] for n in pv["names"])
        # the empty-speaker rule is not treated as an existing name
        assert "targets" in pv and pv["targets"]

        # ---- chained mappings collapse to the final name ----
        chain = {"mappings": [
            {"from": "小李", "to": "李明"},
            {"from": "李明", "to": "李老"},
            {"from": "王师傅", "to": "李老"},
        ]}
        pv = c.post(f"/api/projects/{pid}/speaker-normalization/preview",
                    json=chain).json()
        by_name = {n["name"]: n for n in pv["names"]}
        assert all(n["target"] == "李老" and n["changed"]
                   for n in pv["names"]), pv["names"]
        assert by_name["小李"]["chain"] == ["小李", "李明", "李老"]
        assert pv["stats"] == {
            "nameCount": 3, "changedNameCount": 3,
            "changedSegments": 3, "changedAnnotations": 1,
            "changedRules": 1, "mappingCount": 3, "unusedMappingCount": 0,
        }, pv["stats"]
        target = pv["targets"][0]
        assert target["name"] == "李老"
        assert target["segments"] == 3
        assert target["annotations"] == 1
        assert target["rules"] == 1
        assert sorted(target["sourceNames"]) == ["小李", "李明", "王师傅"]
        # preview must not mutate anything
        untouched = c.get(f"/api/projects/{pid}").json()
        assert untouched["document"]["segments"][0]["speaker"] == "小李"
        assert c.get(f"/api/rules/{r1['id']}").json()["speaker"] == "小李"

        # ---- invalid plans are blocked (empty target / cycle) ----
        bad_empty = c.post(
            f"/api/projects/{pid}/speaker-normalization/preview",
            json={"mappings": [{"from": "小李", "to": "   "}]})
        assert bad_empty.status_code == 400, bad_empty.text
        bad_cycle = c.post(
            f"/api/projects/{pid}/speaker-normalization/preview",
            json={"mappings": [
                {"from": "小李", "to": "李明"},
                {"from": "李明", "to": "小李"},
            ]})
        assert bad_cycle.status_code == 400, bad_cycle.text
        # apply endpoint blocks the same invalid plans
        bad_apply = c.post(
            f"/api/projects/{pid}/speaker-normalization/apply",
            json={"mappings": [{"from": "李明", "to": ""},
                               {"from": "李明", "to": "李明"}]})
        assert bad_apply.status_code == 400, bad_apply.text
        still = c.get(f"/api/projects/{pid}").json()
        assert still["document"]["segments"][1]["speaker"] == "李明"

        # self-mapping is a no-op, not a cycle
        ok_self = c.post(
            f"/api/projects/{pid}/speaker-normalization/preview",
            json={"mappings": [{"from": "李明", "to": "李明"}]})
        assert ok_self.status_code == 200, ok_self.text
        assert ok_self.json()["stats"]["changedNameCount"] == 0

        # ---- unused mapping is reported, blocks nothing ----
        pv_unused = c.post(
            f"/api/projects/{pid}/speaker-normalization/preview",
            json={"mappings": [{"from": "无名氏", "to": "李老"}]})
        assert pv_unused.status_code == 200
        assert pv_unused.json()["unusedMappings"] == [
            {"from": "无名氏", "to": "李老"}]

        # ---- apply renames all three sources in one transaction ----
        res = c.post(f"/api/projects/{pid}/speaker-normalization/apply",
                     json=chain).json()
        assert res["changed"] == {
            "segments": 3, "annotations": 1, "rules": 1}, res["changed"]
        assert res["project"]["id"] == pid
        after = c.get(f"/api/projects/{pid}").json()["document"]
        speakers = [s["speaker"] for s in after["segments"]]
        assert speakers == ["李老", "李老", "李老", ""], speakers
        assert after["annotations"][0]["speaker"] == "李老"
        assert after["annotations"][1]["speaker"] == ""  # empty stays empty
        # ids and other content preserved
        assert [s["id"] for s in after["segments"]] == ["s1", "s2", "s3", "s4"]
        assert after["annotations"][0]["topic"] == "开场白"
        assert after["annotations"][0]["note"] == "x"
        assert after["annotations"][0]["color"] == "#fff"
        rule_after = c.get(f"/api/rules/{r1['id']}").json()
        assert rule_after["speaker"] == "李老"
        assert rule_after["name"] == "小李的话"
        assert c.get(f"/api/rules/{r2['id']}").json()["speaker"] == ""

        # quote search now works under the new name only
        q = c.post(f"/api/projects/{pid}/quote-search",
                   json={"query": "改制", "speaker": "李老"}).json()
        assert q["count"] == 1, q
        assert c.post(f"/api/projects/{pid}/quote-search",
                      json={"query": "改制", "speaker": "李明"}
                      ).json()["count"] == 0

        # re-applying the same mappings changes nothing (idempotent)
        again = c.post(f"/api/projects/{pid}/speaker-normalization/apply",
                       json=chain).json()
        assert again["changed"] == {
            "segments": 0, "annotations": 0, "rules": 0}, again["changed"]

        # ---- project isolation / 404 ----
        assert c.post("/api/projects/nope/speaker-normalization/preview",
                      json={"mappings": []}).status_code == 404
        assert c.post("/api/projects/nope/speaker-normalization/apply",
                      json=chain).status_code == 404

        # other project untouched
        p2 = c.post("/api/projects", json={"name": "另一个"}).json()
        c.put(f"/api/projects/{p2['id']}", json={
            "name": "另一个",
            "document": {"segments": [
                {"id": "x", "start": 0, "end": 1, "speaker": "小李",
                 "text": "q"}], "annotations": []}})
        other = c.get(f"/api/projects/{p2['id']}").json()
        assert other["document"]["segments"][0]["speaker"] == "小李"

        print("ALL SPEAKER NORMALIZATION API TESTS PASSED")


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
