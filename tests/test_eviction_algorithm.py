"""退場演算法的數字要被釘住——它是從 Memmy 搬過來的，不是我設計的。

記憶裡標注「值得整段搬的細節（照描述重寫一定會漏）」，列了三條。這份測試把那
三條各自釘住，因為它們都是「拿掉之後程式照樣跑、只是判斷變得可笑」的那種：

    樣本 ≥3 才用 softmax 加權      少於 3 筆時加權等於讓單一離群值主導
    朝先驗收縮，權重 5             防止 2、3 筆算出誇張分數
    activate 0.02 / archive −0.05  中間留間隙做遲滯，防邊界震盪

這個專案在「尺度混用」上犯過三次同一類 bug（見記憶 xkb-scale-mixing-bug-class），
共同點是隨資料量才浮現。所以這裡測的是**不同樣本數下的具體數值**，不是「有沒有
回傳一個數字」。

2026-09-12 移植時我自己差點誤判一次：看到「兩筆高分 +0.43、五筆高分 +0.48」就
以為先驗收縮沒搬成功。實際上收縮作用在「沒用到」那一側，而那兩組的 without 筆數
不同——差異來自那裡。如果當時照那個數字下結論，會得出錯的判斷。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xkb_eviction as ev  # noqa: E402

O = ev.Observation


class TheThreeDetailsThatGetLostInRewrites(unittest.TestCase):
    def test_softmax_only_kicks_in_at_three_samples(self):
        """兩筆時用普通平均，三筆才加權。

        兩筆 [0.9, 0.3]：普通平均 0.6，會被低分那筆拉下來。
        三筆 [0.9, 0.3, 0.3]：softmax 讓高分權重更大，分數回升。
        少於三筆就加權的話，一筆 0.9 會主導整個判斷。
        """
        without = [O("x", 0.3)]
        two = ev.compute_gain([O("a", 0.9), O("b", 0.3)], without)
        three = ev.compute_gain([O("a", 0.9), O("b", 0.3), O("c", 0.3)], without)

        self.assertLess(two, three)
        self.assertAlmostEqual(two, 0.1333, places=3)
        self.assertAlmostEqual(three, 0.2494, places=3)

    def test_the_prior_shrinks_the_without_side(self):
        """對照樣本少的時候，gain 要被壓得保守。

        這是整段裡最容易漏掉的一條。沒有對照樣本時不該給高分——那正是
        「2、3 筆算出誇張分數」的來源。
        """
        with_obs = [O(str(i), 0.9) for i in range(5)]
        gains = [ev.compute_gain(with_obs, [O(str(i), 0.1) for i in range(n)])
                 for n in (0, 1, 5, 20)]

        # 對照樣本越多，估計值越脫離先驗；而且是單調的。
        self.assertEqual(gains, sorted(gains))
        self.assertAlmostEqual(gains[0], 0.4000, places=3)   # 0 筆，完全靠先驗
        self.assertAlmostEqual(gains[-1], 0.7680, places=3)  # 20 筆

    def test_the_prior_weight_is_five(self):
        """權重寫死成 5 不是隨手取的——它決定「要幾筆才脫離先驗」。

        直接驗算式：without 一筆 0.1、baseline 被夾在 0.2..0.5。
        """
        self.assertEqual(ev.PRIOR_WEIGHT, 5)
        with_obs = [O("a", 0.5)]
        one = ev.compute_gain(with_obs, [O("x", 0.1)])
        # (0.1×1 + baseline×5) / (1+5)，baseline = pool mean 夾在 [0.2, 0.5]
        pool = (0.5 + 0.1) / 2          # 0.3，在範圍內
        expected = 0.5 - (0.1 * 1 + pool * 5) / 6
        self.assertAlmostEqual(one, expected, places=6)

    def test_the_baseline_is_clamped(self):
        """全部都是滿分時 baseline 不會跟著衝到 1.0，上限 0.5。"""
        high = ev.compute_gain([O(str(i), 1.0) for i in range(5)],
                               [O(str(i), 1.0) for i in range(5)])
        # with 與 without 一樣高，gain 應該接近 0 而不是負很多。
        self.assertGreater(high, -0.1)
        self.assertEqual((ev.BASELINE_FLOOR, ev.BASELINE_CEILING), (0.2, 0.5))


class HysteresisStopsOscillation(unittest.TestCase):
    def test_there_is_a_gap_between_activate_and_archive(self):
        self.assertGreater(ev.MIN_GAIN, ev.ARCHIVE_GAIN)
        self.assertAlmostEqual(ev.MIN_GAIN - ev.ARCHIVE_GAIN, 0.07, places=6)

    def test_a_gain_in_the_gap_changes_nothing(self):
        """gain 落在間隙裡時，active 留著、candidate 也留著——不會反覆翻面。"""
        for gain in (0.0, 0.01, -0.03, -0.049):
            with self.subTest(gain=gain):
                self.assertEqual(
                    ev.status_after_gain("active", support=3, gain=gain), "active")
                self.assertEqual(
                    ev.status_after_gain("candidate", support=3, gain=gain), "candidate")

    def test_clearly_bad_gain_archives(self):
        self.assertEqual(
            ev.status_after_gain("active", support=3, gain=-0.2), "archived")

    def test_clearly_good_gain_activates(self):
        self.assertEqual(
            ev.status_after_gain("candidate", support=3, gain=0.3), "active")

    def test_losing_support_archives_even_with_good_gain(self):
        self.assertEqual(
            ev.status_after_gain("active", support=0, gain=0.9), "archived")


class EvictionIsNotAOneWayDoor(unittest.TestCase):
    """這是我們跟 Memmy 刻意不同的一處。

    Memmy 的 policyStatusAfterGain 第一行是 `if archived return archived`——
    一旦退場就不會自己回來。它有 reward pipeline 能重新評估，XKB 沒有。

    而單行道的代價 2026-09-11 剛付過一次：excluded 旗標做成不可逆，結果一張被
    解析 bug 誤排除的好卡片救不回來，只能手動改檔案。
    """

    def test_a_recovered_gain_revives_an_archived_item(self):
        self.assertEqual(
            ev.status_after_gain("archived", support=3, gain=0.3), "candidate")

    def test_it_revives_to_candidate_not_straight_to_active(self):
        """要再證明一次才算 active，不是一回來就恢復全部權重。"""
        revived = ev.status_after_gain("archived", support=3, gain=0.3)
        self.assertEqual(revived, "candidate")

    def test_the_revive_threshold_is_higher_than_activate(self):
        """不然剛退場的東西會在邊界上彈回來。"""
        self.assertGreater(ev.REVIVE_GAIN, ev.MIN_GAIN)
        # 剛好在 activate 門檻上的 gain 不足以復活。
        self.assertEqual(
            ev.status_after_gain("archived", support=3, gain=ev.MIN_GAIN), "archived")

    def test_a_still_bad_gain_stays_archived(self):
        self.assertEqual(
            ev.status_after_gain("archived", support=3, gain=-0.2), "archived")


class TheRealXkbCases(unittest.TestCase):
    """用 knowledge_usage 裡真實的兩筆，確認它們被分開。

    退場不是「很久沒用到」——這兩筆都非常活躍。
    """

    def test_often_offered_rarely_used_is_archived(self):
        """被考慮 236 次、只被注入 56 次（命中率 24%）。

        它每次語意搜尋都被撈出來，一點都不「久沒被使用」，但四分之三的時候
        被判斷為不該用——一直佔召回名額卻沒貢獻。
        """
        hit_rate = 56 / 236
        with_obs = [O(str(i), hit_rate) for i in range(8)]
        without_obs = [O(str(i), 0.5) for i in range(8)]

        gain = ev.compute_gain(with_obs, without_obs)

        self.assertLess(gain, ev.ARCHIVE_GAIN)
        self.assertEqual(ev.status_after_gain("active", support=8, gain=gain),
                         "archived")

    def test_often_offered_often_used_stays_active(self):
        hit_rate = 183 / 267
        with_obs = [O(str(i), hit_rate) for i in range(8)]
        without_obs = [O(str(i), 0.4) for i in range(8)]

        gain = ev.compute_gain(with_obs, without_obs)

        self.assertGreater(gain, ev.MIN_GAIN)
        self.assertEqual(ev.status_after_gain("active", support=8, gain=gain),
                         "active")


class Smoothing(unittest.TestCase):
    def test_the_first_observation_is_not_smoothed(self):
        """沒有歷史可平滑時直接用新值，否則第一次會被一個憑空的 0 拉低。"""
        self.assertEqual(ev.smooth_gain(0.5, 0.0, is_first=True), 0.5)

    def test_later_observations_move_gradually(self):
        moved = ev.smooth_gain(1.0, 0.0, is_first=False)
        self.assertAlmostEqual(moved, ev.EMA_ALPHA, places=6)


class DegenerateInputsDoNotCrash(unittest.TestCase):
    def test_no_observations_at_all(self):
        gain = ev.compute_gain([], [])
        self.assertFalse(gain != gain, "gain 不可以是 NaN")

    def test_only_without_observations(self):
        gain = ev.compute_gain([], [O("x", 0.5)])
        self.assertFalse(gain != gain, "gain 不可以是 NaN")

    def test_evaluate_end_to_end(self):
        status, gain = ev.evaluate(
            "k", with_obs=[O(str(i), 0.8) for i in range(4)],
            without_obs=[O(str(i), 0.3) for i in range(4)],
            current_status="candidate")
        self.assertEqual(status, "active")
        self.assertGreater(gain, ev.MIN_GAIN)


if __name__ == "__main__":
    unittest.main()
