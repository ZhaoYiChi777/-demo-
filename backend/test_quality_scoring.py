import json
from pathlib import Path
import subprocess
import unittest
from quality_scoring import quality_score, dimension_key


def fixture():
    return {'checks':[dict(id=str(i), category=cat, rule={'mode':mode}, result=result)
        for i,(cat,mode,result) in enumerate([('common','presence','pass'),('common','manual','pending'),
                                            ('special','presence','fail'),('topic','presence','pass')])]}


class QualityTests(unittest.TestCase):
    def test_partition_and_weighted_deductions(self):
        run=fixture()
        self.assertEqual([dimension_key(c) for c in run['checks']],['material','common','special','topic'])
        result=quality_score(run)
        self.assertEqual(result['total'],52.5)
        self.assertTrue(result['provisional'])
        run['checks'][1]['result']='pass'
        self.assertEqual(quality_score(run)['total'],65)
        run['checks'][2]['result']='pass'
        self.assertEqual(quality_score(run)['total'],100)
        run['checks'].pop()
        self.assertIsNone(quality_score(run)['total'])

    def test_browser_legacy_calculation_matches_backend(self):
        root=Path(__file__).resolve().parents[1]
        runs=[]
        for result in ('pass','fail','pending'):
            run=fixture();run['checks'][1]['result']=result;runs.append(run)
        runs.append({'checks':fixture()['checks'][:-1]})
        script="const fs=require('fs'),vm=require('vm');global.document={addEventListener(){}};vm.runInThisContext(fs.readFileSync('quality-score.js','utf8'));let runs=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(runs.map(qualityScore)));"
        output=subprocess.check_output(['node','-e',script],input=json.dumps(runs).encode(),cwd=root)
        browser=json.loads(output)
        for run,actual in zip(runs,browser):
            expected=quality_score(run)
            for key in ('total','dimensions','provisional','policy'):self.assertEqual(actual[key],expected[key])
