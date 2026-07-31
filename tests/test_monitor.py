import unittest

from monitor import (
    PROTECTION_TRIGGER_SQL,
    Rate,
    Sample,
    _evaluate_trigger_sql,
    _sql_hash,
    calc_rate,
)


GOOD_SQL = """CREATE TRIGGER block_log_inserts
BEFORE INSERT ON logs
BEGIN
  SELECT RAISE(IGNORE);
END"""


class TriggerEvaluationTests(unittest.TestCase):
    """验证 _evaluate_trigger_sql 能识别各种篡改和绕过场景。"""

    def test_good_trigger_passes(self):
        ok, issue = _evaluate_trigger_sql(GOOD_SQL)
        self.assertTrue(ok, f"规范触发器应该通过校验，但报：{issue}")
        self.assertEqual(issue, "")

    def test_empty_sql_fails(self):
        ok, issue = _evaluate_trigger_sql("")
        self.assertFalse(ok)
        self.assertIn("为空", issue)

    def test_after_insert_fails(self):
        """AFTER INSERT 拦不住已经写入的行。"""
        sql = GOOD_SQL.replace("BEFORE INSERT", "AFTER INSERT")
        ok, issue = _evaluate_trigger_sql(sql)
        self.assertFalse(ok)
        self.assertIn("BEFORE INSERT", issue)

    def test_before_update_fails(self):
        """BEFORE UPDATE 不拦 INSERT。"""
        sql = GOOD_SQL.replace("BEFORE INSERT", "BEFORE UPDATE")
        ok, issue = _evaluate_trigger_sql(sql)
        self.assertFalse(ok)
        self.assertIn("BEFORE INSERT", issue)

    def test_wrong_table_fails(self):
        """触发器挂在别的表上，对 logs 无效。"""
        sql = GOOD_SQL.replace("ON logs", "ON other_table")
        ok, issue = _evaluate_trigger_sql(sql)
        self.assertFalse(ok)
        self.assertIn("logs", issue)

    def test_when_clause_fails(self):
        """带 WHEN 条件的触发器存在绕过路径。"""
        sql = GOOD_SQL.replace("BEGIN", "WHEN 1=0 BEGIN")
        ok, issue = _evaluate_trigger_sql(sql)
        self.assertFalse(ok)
        self.assertIn("WHEN", issue)

    def test_missing_raise_fails(self):
        """没有 RAISE(IGNORE) 的触发器不会阻止写入。"""
        sql = GOOD_SQL.replace("SELECT RAISE(IGNORE);", "SELECT 1;")
        ok, issue = _evaluate_trigger_sql(sql)
        self.assertFalse(ok)
        self.assertIn("RAISE(IGNORE)", issue)

    def test_trigger_with_persistent_update_fails(self):
        """旧计数触发器会持续写盘，必须被识别并替换。"""
        sql = GOOD_SQL.replace(
            "SELECT RAISE(IGNORE);",
            "UPDATE _monitor_blocked SET count = count + 1; SELECT RAISE(IGNORE);",
        )
        ok, issue = _evaluate_trigger_sql(sql)
        self.assertFalse(ok)
        self.assertIn("写操作", issue)

    def test_case_insensitive_matching(self):
        """SQL 大小写不应影响校验。"""
        sql = GOOD_SQL.upper()
        ok, _ = _evaluate_trigger_sql(sql)
        self.assertTrue(ok)

    def test_protection_trigger_has_no_persistent_write(self):
        """保护触发器只能丢弃 INSERT，不能更新计数表或其他持久数据。"""
        upper = PROTECTION_TRIGGER_SQL.upper()
        self.assertIn("RAISE(IGNORE)", upper)
        self.assertNotIn("UPDATE", upper)
        self.assertNotIn("INSERT INTO", upper)
        self.assertNotIn("DELETE", upper)


class SqlHashTests(unittest.TestCase):
    def test_same_sql_same_hash(self):
        self.assertEqual(_sql_hash(GOOD_SQL), _sql_hash(GOOD_SQL))

    def test_different_sql_different_hash(self):
        self.assertNotEqual(_sql_hash(GOOD_SQL), _sql_hash(GOOD_SQL + " "))

    def test_hash_is_hex(self):
        h = _sql_hash(GOOD_SQL)
        self.assertEqual(len(h), 16)
        int(h, 16)  # 不抛异常即合法十六进制


class MonitorRateTests(unittest.TestCase):
    def test_rate_uses_elapsed_time(self):
        previous = Sample(ts=10, max_id=100, wal_bytes=1000, database_available=True)
        current = Sample(ts=12, max_id=110, wal_bytes=3048, database_available=True)
        self.assertEqual(calc_rate(previous, current), Rate(row_rate=5, wal_rate=1024))

    def test_rate_does_not_report_negative_growth(self):
        previous = Sample(ts=10, max_id=100, wal_bytes=4096, database_available=True)
        current = Sample(ts=11, max_id=90, wal_bytes=0, database_available=True)
        self.assertEqual(calc_rate(previous, current), Rate())

    def test_invalid_sample_has_no_rate(self):
        previous = Sample(ts=10, database_available=False)
        current = Sample(ts=11, max_id=100, database_available=True)
        self.assertEqual(calc_rate(previous, current), Rate())


if __name__ == "__main__":
    unittest.main()
