"""Feature-specific import safety checks; no AWS credentials required."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImportContractTests(unittest.TestCase):
    def test_legacy_policy_version_warning_is_suppressed_only_for_bucket_policy(self):
        template = (ROOT / 'cloudformation/frontend.yaml').read_text()
        policy = template.split('  AudigoFrontendBucketPolicy:\n', 1)[1]
        self.assertIn("Version: '2008-10-17'", policy)
        self.assertIn('Metadata:\n      cfn-lint:\n        config:\n          ignore_checks:\n            - W2511', policy)
        self.assertEqual(1, template.count('- W2511'))
        body = json.loads((ROOT / 'deploy/imports/frontend.json').read_text())
        self.assertEqual({'cfn-lint': {'config': {'ignore_checks': ['W2511']}}},
                         body['Resources']['AudigoFrontendBucketPolicy']['Metadata'])
        self.assertEqual('2008-10-17', body['Resources']['AudigoFrontendBucketPolicy']['Properties']['PolicyDocument']['Version'])

    def test_frontend_ascii_import_copy_preserves_unicode_and_intrinsics(self):
        raw = (ROOT / 'deploy/imports/frontend.json').read_bytes()
        self.assertTrue(raw.isascii())
        body = json.loads(raw.decode('ascii'))
        self.assertNotIn('Outputs', body)
        resources = body['Resources']
        self.assertEqual(4, len(resources))
        for resource in resources.values():
            self.assertEqual('Retain', resource['DeletionPolicy'])
            self.assertEqual('Retain', resource['UpdateReplacePolicy'])
        distribution = resources['AudigoFrontendDistribution']['Properties']['DistributionConfig']
        self.assertEqual('어디고 서비스의 프론트 배포입니다.', distribution['Comment'])
        self.assertNotIn('CloudFrontDefaultCertificate', distribution['ViewerCertificate'])
        self.assertEqual({'Fn::GetAtt': ['AudigoFrontendBucket', 'RegionalDomainName']},
                         distribution['Origins'][0]['DomainName'])
        self.assertEqual({'Fn::GetAtt': ['AudigoFrontendOriginAccessControl', 'Id']},
                         distribution['Origins'][0]['OriginAccessControlId'])
        policy = resources['AudigoFrontendBucketPolicy']['Properties']
        self.assertEqual({'Ref': 'AudigoFrontendBucket'}, policy['Bucket'])
        self.assertEqual({'Fn::Sub': 'arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${AudigoFrontendDistribution}'},
                         policy['PolicyDocument']['Statement'][0]['Condition']['StringEquals']['AWS:SourceArn'])

    def test_frontend_acm_certificate_does_not_set_other_certificate_selectors(self):
        template = (ROOT / 'cloudformation/frontend.yaml').read_text()
        self.assertEqual(1, len(re.findall(r'(?m)^\s+AcmCertificateArn:', template)))
        self.assertNotRegex(template, r'(?m)^\s+(CloudFrontDefaultCertificate|IamCertificateId):')

    def test_frontend_import_preserves_security_and_stays_disabled(self):
        config = json.loads((ROOT / 'deploy/stacks.json').read_text())
        frontend = next(s for s in config['stacks'] if s['name'] == 'audigo-frontend')
        self.assertFalse(frontend['enabled'])
        self.assertFalse(frontend['allow_create'])
        self.assertEqual('', frontend['role_arn'])
        template = (ROOT / frontend['template']).read_text()
        self.assertNotRegex(template, r'(?m)^Outputs:')
        self.assertNotRegex(template, r'(?m)^\s+(AccessControl|VersioningConfiguration):')
        self.assertEqual(4, template.count('DeletionPolicy: Retain'))
        self.assertEqual(4, template.count('UpdateReplacePolicy: Retain'))
        for resource in ('AWS::S3::Bucket', 'AWS::S3::BucketPolicy',
                         'AWS::CloudFront::OriginAccessControl', 'AWS::CloudFront::Distribution'):
            self.assertIn('Type: ' + resource + '\n', template)
        self.assertIn('BlockedEncryptionTypes:', template)
        self.assertIn('- SSE-C', template)
        self.assertIn('MinimumProtocolVersion: TLSv1.2_2025', template)
        self.assertIn('global/webacl/CreatedByCloudFront-722e4b05/397f3726-c207-415e-bd96-7b0ecc24f184', template)
        self.assertIn('AWS:SourceArn:', template)
        self.assertIn('distribution/${AudigoFrontendDistribution}', template)


if __name__ == "__main__":
    unittest.main()
