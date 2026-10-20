import unittest

from main import infer_specialty


class SpecialtyInferenceTests(unittest.TestCase):
    def test_turbine_terms(self):
        primary, _, result = infer_specialty("汽轮机 DEH 调速汽门 EH油 轴系保护")
        self.assertEqual(primary, "TURB")
        self.assertGreaterEqual(result["confidence"], 0.72)
        self.assertEqual(result["status"], "auto_confirmed")

    def test_electrical_terms(self):
        primary, _, result = infer_specialty("发电机励磁系统、继电保护和GIS改造")
        self.assertEqual(primary, "ELEC")
        self.assertEqual(result["status"], "auto_confirmed")

    def test_boiler_terms(self):
        primary, _, result = infer_specialty("锅炉受热面与磨煤机制粉系统检修")
        self.assertEqual(primary, "BOIL")

    def test_unknown_requires_no_false_specialty(self):
        primary, _, result = infer_specialty("办公楼照明与会议室装修")
        self.assertEqual(primary, "UNKNOWN")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["confidence"], 0.0)

    def test_cross_specialty_is_reviewable(self):
        primary, _, result = infer_specialty("汽轮机DEH与发电机励磁系统联合改造")
        self.assertEqual({primary, *result["related"]}, {"TURB", "ELEC"})
        self.assertEqual(result["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
