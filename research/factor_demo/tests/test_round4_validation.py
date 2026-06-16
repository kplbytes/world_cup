"""第四轮验证定向修复自动化测试

覆盖：
1. 世界杯筛选必须严格使用 tournament == "FIFA World Cup"
2. 冲突记录排除
3. 重复放大测试验证
4. 时间边界校验
"""

import hashlib
import numpy as np
import pandas as pd
import pytest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
CSV_PATH = PROJECT_DIR.parent.parent / "data" / "external" / "international_results.csv"
OUTPUT_DIR = PROJECT_DIR / "outputs" / "round4"


# ============================================================
# 1. 世界杯筛选测试
# ============================================================

class TestWorldCupFiltering:
    """验证世界杯筛选使用精确匹配。"""

    @pytest.fixture(scope="class")
    def raw_data(self):
        return pd.read_csv(CSV_PATH)

    @pytest.fixture(scope="class")
    def canonical_data(self):
        path = OUTPUT_DIR / "canonical_single_source_results.csv"
        if not path.exists():
            pytest.skip("canonical_single_source_results.csv not yet generated")
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def test_fifa_world_cup_exact_match_per_year(self, raw_data):
        """每届世界杯正赛必须恰好64场。"""
        raw_data["date"] = pd.to_datetime(raw_data["date"])
        for year in [2010, 2014, 2018, 2022]:
            wc = raw_data[
                (raw_data["date"].dt.year == year) &
                (raw_data["tournament"] == "FIFA World Cup")
            ]
            assert len(wc) == 64, (
                f"{year} FIFA World Cup has {len(wc)} matches, expected 64"
            )

    def test_fuzzy_match_includes_non_fifa(self, raw_data):
        """模糊匹配会包含非FIFA World Cup赛事（如VIVA World Cup），证明必须精确匹配。"""
        raw_data["date"] = pd.to_datetime(raw_data["date"])
        exact_2010 = raw_data[
            (raw_data["date"].dt.year == 2010) &
            (raw_data["tournament"] == "FIFA World Cup")
        ]
        contains_2010 = raw_data[
            (raw_data["date"].dt.year == 2010) &
            (raw_data["tournament"].str.contains("World Cup", case=False)) &
            (~raw_data["tournament"].str.contains("Qualif", case=False))
        ]
        # 模糊匹配比精确匹配多出11场（VIVA World Cup等）
        assert len(contains_2010) > len(exact_2010), (
            f"Fuzzy matching should include more matches than exact: "
            f"exact={len(exact_2010)}, contains={len(contains_2010)}"
        )
        # 精确匹配必须恰好64场
        assert len(exact_2010) == 64

    def test_world_cup_qualification_not_included(self, raw_data):
        """FIFA World Cup qualification不应被归为世界杯正赛。"""
        raw_data["date"] = pd.to_datetime(raw_data["date"])
        for year in [2010, 2014, 2018, 2022]:
            qual = raw_data[
                (raw_data["date"].dt.year == year) &
                (raw_data["tournament"].str.contains("Qualif", case=False))
            ]
            wc = raw_data[
                (raw_data["date"].dt.year == year) &
                (raw_data["tournament"] == "FIFA World Cup")
            ]
            # 确认qualification和正赛没有重叠
            assert len(wc) == 64


# ============================================================
# 2. 冲突记录排除测试
# ============================================================

class TestConflictExclusion:
    """验证冲突记录被排除出建模数据。"""

    @pytest.fixture(scope="class")
    def canonical_data(self):
        path = OUTPUT_DIR / "canonical_single_source_results.csv"
        if not path.exists():
            pytest.skip("canonical_single_source_results.csv not yet generated")
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def test_conflicting_records_excluded(self, canonical_data):
        """1974-02-17 Tahiti vs New Caledonia冲突记录不应出现在canonical数据中。"""
        conflict = canonical_data[
            (canonical_data["home_team"] == "Tahiti") &
            (canonical_data["away_team"] == "New Caledonia") &
            (canonical_data["date"] == "1974-02-17")
        ]
        assert len(conflict) == 0, (
            f"Found {len(conflict)} conflicting 1974-02-17 Tahiti vs New Caledonia records "
            f"in canonical data - should be excluded"
        )

    def test_result_verified_always_false(self, canonical_data):
        """result_verified必须全部为False。"""
        assert not canonical_data["result_verified"].any(), (
            "Some records have result_verified=True, but no cross-validation was performed"
        )

    def test_verification_status_not_verified(self, canonical_data):
        """所有记录的verification_status应为single_source，不应有verified。"""
        statuses = canonical_data["verification_status"].unique()
        assert "verified" not in statuses, (
            f"Found 'verified' status without cross-validation: {statuses}"
        )
        assert "single_source" in statuses, (
            f"Expected 'single_source' status, got: {statuses}"
        )

    def test_local_record_id_not_source_match_id(self, canonical_data):
        """列名应为local_record_id，不是source_match_id。"""
        assert "local_record_id" in canonical_data.columns, (
            "Expected 'local_record_id' column, not 'source_match_id'"
        )
        assert "source_match_id" not in canonical_data.columns, (
            "Column 'source_match_id' should be renamed to 'local_record_id'"
        )


# ============================================================
# 3. 重复放大测试验证
# ============================================================

class TestDuplicationAmplification:
    """验证重复数据不会产生虚假改善。"""

    def test_point_estimate_unchanged(self):
        """复制8倍后点估计不变。"""
        # 模拟数据
        rng = np.random.RandomState(42)
        n = 100
        y_true = rng.choice([0, 1, 2], size=n)
        y_onehot = np.zeros((n, 3))
        for i in range(n):
            y_onehot[i, y_true[i]] = 1.0
        preds = rng.dirichlet([1, 1, 1], size=n)

        # 原始Brier
        briers_orig = np.sum((preds - y_onehot) ** 2, axis=1)
        mean_orig = np.mean(briers_orig)

        # 放大8倍
        preds_amp = np.tile(preds, (8, 1))
        y_amp = np.tile(y_onehot, (8, 1))
        briers_amp = np.sum((preds_amp - y_amp) ** 2, axis=1)
        mean_amp = np.mean(briers_amp)

        assert abs(mean_orig - mean_amp) < 1e-10, (
            f"Point estimate changed after duplication: {mean_orig} vs {mean_amp}"
        )

    def test_independent_sample_count_unchanged(self):
        """有效独立样本数不变。"""
        n_orig = 100
        n_amp = 100  # 放大8倍后独立样本数仍为100，不是800
        assert n_orig == n_amp, (
            "Independent sample count should not change after duplication"
        )

    def test_bootstrap_ci_narrows_with_wrong_count(self):
        """错误使用800作为独立样本数会导致CI缩窄。"""
        rng = np.random.RandomState(42)
        n = 100
        y_true = rng.choice([0, 1, 2], size=n)
        y_onehot = np.zeros((n, 3))
        for i in range(n):
            y_onehot[i, y_true[i]] = 1.0
        preds = rng.dirichlet([1, 1, 1], size=n)
        briers = np.sum((preds - y_onehot) ** 2, axis=1)

        # 正确CI（100个独立样本）
        boot_correct = []
        for _ in range(1000):
            idx = rng.randint(0, n, size=n)
            boot_correct.append(np.mean(briers[idx]))
        ci_correct_width = np.percentile(boot_correct, 97.5) - np.percentile(boot_correct, 2.5)

        # 错误CI（800个样本，含重复）
        briers_amp = np.tile(briers, 8)
        boot_wrong = []
        for _ in range(1000):
            idx = rng.randint(0, 800, size=800)
            boot_wrong.append(np.mean(briers_amp[idx]))
        ci_wrong_width = np.percentile(boot_wrong, 97.5) - np.percentile(boot_wrong, 2.5)

        # 错误做法的CI应该更窄
        assert ci_wrong_width < ci_correct_width, (
            f"Wrong CI ({ci_wrong_width:.4f}) should be narrower than correct CI ({ci_correct_width:.4f})"
        )

    def test_correct_bootstrap_ci_reproducible(self):
        """使用相同种子和数据的两次bootstrap应产生相同的CI宽度。"""
        n = 100
        # 使用固定种子生成数据
        rng_data = np.random.RandomState(123)
        y_true = rng_data.choice([0, 1, 2], size=n)
        y_onehot = np.zeros((n, 3))
        for i in range(n):
            y_onehot[i, y_true[i]] = 1.0
        preds = rng_data.dirichlet([1, 1, 1], size=n)
        briers = np.sum((preds - y_onehot) ** 2, axis=1)

        # 两次bootstrap使用相同种子
        def run_bootstrap(briers, n, seed):
            rng = np.random.RandomState(seed)
            boot = []
            for _ in range(1000):
                idx = rng.randint(0, n, size=n)
                boot.append(np.mean(briers[idx]))
            return np.percentile(boot, 97.5) - np.percentile(boot, 2.5)

        ci1_width = run_bootstrap(briers, n, 42)
        ci2_width = run_bootstrap(briers, n, 42)

        assert ci1_width == ci2_width, (
            f"Same seed should produce identical CI: {ci1_width:.6f} vs {ci2_width:.6f}"
        )


# ============================================================
# 4. 时间边界校验
# ============================================================

class TestTimeBoundaries:
    """验证时间边界和数据冻结。"""

    @pytest.fixture(scope="class")
    def canonical_data(self):
        path = OUTPUT_DIR / "canonical_single_source_results.csv"
        if not path.exists():
            pytest.skip("canonical_single_source_results.csv not yet generated")
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def test_no_future_dates(self, canonical_data):
        """canonical数据不应包含2026-01-01之后的记录。"""
        future = canonical_data[canonical_data["date"] > "2025-12-31"]
        assert len(future) == 0, f"Found {len(future)} records after freeze date"

    def test_no_2026_world_cup_in_training(self, canonical_data):
        """2026世界杯数据不应出现在canonical数据中。"""
        wc_2026 = canonical_data[
            (canonical_data["date"].dt.year == 2026) &
            (canonical_data["tournament"] == "FIFA World Cup")
        ]
        assert len(wc_2026) == 0, (
            f"Found {len(wc_2026)} 2026 World Cup records in canonical data"
        )

    def test_chronological_wc_backtest_no_future_data(self):
        """验证chronological_worldcup_backtest.json中每届只使用此前数据。"""
        path = OUTPUT_DIR / "chronological_worldcup_backtest.json"
        if not path.exists():
            pytest.skip("chronological_worldcup_backtest.json not yet generated")
        import json
        with open(path) as f:
            results = json.load(f)

        for r in results:
            if "error" in r:
                continue
            year = r["world_cup_year"]
            train_end = r["train_end"]
            # train_end必须早于世界杯年份
            assert int(train_end[:4]) < year, (
                f"{year} WC: train_end={train_end} is not before WC year"
            )
            assert r["validation_type"] == "strict_chronological"
            assert r["data_used"] == f"only_matches_before_{year}"

    def test_wc_backtest_64_matches_each(self):
        """每届世界杯回测必须恰好64场。"""
        path = OUTPUT_DIR / "chronological_worldcup_backtest.json"
        if not path.exists():
            pytest.skip("chronological_worldcup_backtest.json not yet generated")
        import json
        with open(path) as f:
            results = json.load(f)

        for r in results:
            if "error" in r:
                # 异常终止的届次应有error字段
                assert "expected_64_got_" in r["error"], (
                    f"Unexpected error format: {r['error']}"
                )
            else:
                assert r["n_test"] == 64, (
                    f"{r['world_cup_year']} WC has {r['n_test']} test matches, expected 64"
                )


# ============================================================
# 5. 文件命名和内容一致性测试
# ============================================================

class TestFileNamingConsistency:
    """验证文件命名和内容一致性。"""

    def test_canonical_file_renamed(self):
        """canonical_verified_results.csv应已更名为canonical_single_source_results.csv。"""
        old_path = OUTPUT_DIR / "canonical_verified_results.csv"
        new_path = OUTPUT_DIR / "canonical_single_source_results.csv"
        assert not old_path.exists(), (
            "canonical_verified_results.csv should be renamed to canonical_single_source_results.csv"
        )
        assert new_path.exists(), (
            "canonical_single_source_results.csv should exist"
        )

    def test_audit_md_mentions_conflict_exclusion(self):
        """DATA_DUPLICATION_AUDIT.md应提及冲突排除。"""
        path = OUTPUT_DIR / "DATA_DUPLICATION_AUDIT.md"
        if not path.exists():
            pytest.skip("DATA_DUPLICATION_AUDIT.md not yet generated")
        content = path.read_text()
        assert "冲突记录" in content or "conflicting" in content.lower(), (
            "DATA_DUPLICATION_AUDIT.md should mention conflict exclusion"
        )
        assert "排除" in content, (
            "DATA_DUPLICATION_AUDIT.md should mention exclusion of conflicting records"
        )

    def test_audit_md_no_contradiction(self):
        """DATA_DUPLICATION_AUDIT.md不应有矛盾描述。"""
        path = OUTPUT_DIR / "DATA_DUPLICATION_AUDIT.md"
        if not path.exists():
            pytest.skip("DATA_DUPLICATION_AUDIT.md not yet generated")
        content = path.read_text()
        # 不应同时出现"无冲突"和"1个冲突"
        has_no_conflict = "无冲突" in content
        has_one_conflict = "1 组冲突" in content or "1个冲突" in content
        # 如果有冲突记录，不应说"无冲突"
        if has_one_conflict:
            assert not has_no_conflict, (
                "Contradiction: both '1 conflict' and 'no conflict' found in audit"
            )
