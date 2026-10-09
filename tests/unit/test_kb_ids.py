"""kb 化 id 单元测试：遗留形态、分区形态、无碰撞、paper_identity。

红线：``kb_id`` 为 None/空/``"default"`` 时 id 形态必须与升级前完全一致，
实包 id 内嵌 ``{kb}:`` 前缀——同一实体/论文在不同领域包内分立、互不合并。
"""

import re

from src.knowledge.graph_store import make_entity_id
from src.tools.paper_schema import make_paper_id, paper_identity, title_hash

KB_A = "kb_aaaaaaaa"
KB_B = "kb_bbbbbbbb"

LEGACY_ENTITY = re.compile(r"^e:[0-9a-f]{12}$")
PARTITIONED_ENTITY = re.compile(r"^e:kb_[0-9a-f]{8}:[0-9a-f]{12}$")


class TestEntityIds:
    def test_legacy_form_for_empty_and_default(self):
        for kb in (None, "", "  ", "default"):
            assert LEGACY_ENTITY.match(make_entity_id("FedProx", kb))

    def test_legacy_equals_explicit_default(self):
        assert make_entity_id("FedProx") == make_entity_id("FedProx", "default")

    def test_partitioned_form(self):
        eid = make_entity_id("FedProx", KB_A)
        assert PARTITIONED_ENTITY.match(eid)
        assert eid.startswith(f"e:{KB_A}:")

    def test_deterministic_within_pack(self):
        assert make_entity_id("Federated  Learning!", KB_A) == make_entity_id("Federated  Learning!", KB_A)

    def test_same_name_across_packs_distinct(self):
        ids = {make_entity_id("FedProx"), make_entity_id("FedProx", KB_A), make_entity_id("FedProx", KB_B)}
        assert len(ids) == 3

    def test_no_collision_across_names_and_packs(self):
        names = ["FedProx", "CIFAR-10", "精度", "GNN", "Meta-Learning"]
        ids = {make_entity_id(n, kb) for n in names for kb in ("", "default", KB_A, KB_B)}
        assert len(ids) == len(names) * 3  # 空串与 default 同形 → 每个名字 3 个 id


class TestPaperIds:
    PAPER = {"arxiv_id": "2401.12345", "title": "A Survey of FedProx"}

    def test_arxiv_legacy_and_partitioned(self):
        assert make_paper_id(self.PAPER) == "ax:2401.12345"
        assert make_paper_id(self.PAPER, "default") == "ax:2401.12345"
        assert make_paper_id(self.PAPER, KB_A) == f"ax:{KB_A}:2401.12345"

    def test_title_hash_fallback(self):
        paper = {"title": "A Survey of FedProx"}
        th = title_hash("A Survey of FedProx")
        assert make_paper_id(paper) == f"th:{th}"
        assert make_paper_id(paper, KB_A) == f"th:{KB_A}:{th}"

    def test_url_fallback_and_empty(self):
        paper = {"title": "", "url": "https://example.org/paper"}
        assert make_paper_id(paper, KB_A).startswith(f"url:{KB_A}:")
        assert make_paper_id({}) == ""

    def test_paper_identity_bundle(self):
        ident = paper_identity(self.PAPER, KB_A)
        assert ident["paper_id"] == f"ax:{KB_A}:2401.12345"
        assert ident["arxiv_id"] == "2401.12345"
        assert ident["title_hash"] == title_hash(self.PAPER["title"])  # 文本指纹不分区
        assert paper_identity(self.PAPER)["paper_id"] == "ax:2401.12345"

    def test_same_paper_two_packs_distinct_ids(self):
        assert make_paper_id(self.PAPER, KB_A) != make_paper_id(self.PAPER, KB_B)
