"""IEF contract-only fixtures, isolation negatives and schema mirror validation."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[1]

def validate_ief_contracts(document: dict) -> None:
    base = 'https://agentic-trading-lab.invalid/ief-openapi'
    registry = Registry().with_resource(base, Resource.from_contents(document, default_specification=DRAFT202012))
    def validator(component):
        return Draft202012Validator({'$ref': base + '#/components/schemas/' + component}, registry=registry, format_checker=contract_formats())
    examples = ROOT / 'contracts/examples/v1'
    manifest = json.loads((examples / 'ief-fixtures.json').read_text('utf-8'))
    samples = {}
    negative_count = 0
    for filename, component in manifest.items():
        value = json.loads((examples / filename).read_text('utf-8'))
        check = validator(component)
        check.validate(value)
        samples[component] = value
        invalid = copy.deepcopy(value)
        invalid['unexpectedField'] = 'denied'
        assert not check.is_valid(invalid), component + ' accepts unknown fields'
        negative_count += 1
    for component in ('InvestmentAllowedActionV1', 'InvestmentSourceReferenceV1'):
        invalid = copy.deepcopy(samples[component]); invalid['url'] = 'https://foreign.invalid/'
        assert not validator(component).is_valid(invalid)
        negative_count += 1
    for field,value in [('value', 12.5),('value','1e3'),('asOf','2026-11-23T12:00:00+01:00')]:
        invalid = copy.deepcopy(samples['InvestmentMetricValueV1']);invalid[field]=value
        assert not validator('InvestmentMetricValueV1').is_valid(invalid)
        negative_count += 1
    invalid = copy.deepcopy(samples['InvestmentMetricValueV1']);invalid['value']=None;invalid['reasonCodes']=[]
    assert not validator('InvestmentMetricValueV1').is_valid(invalid)
    negative_count += 1
    for component in ('PortfolioProcessingPreviewRequestV1','PortfolioProcessingPreviewV1','PortfolioProcessingSendV1','PortfolioProcessingDeclineV1','PortfolioProcessingRevokeV1','PortfolioProcessingStatusV1'):
        invalid = copy.deepcopy(samples[component]);invalid['purpose']='HYPOTHESIS_INTERPRETATION_V1'
        assert not validator(component).is_valid(invalid)
        negative_count += 1
    invalid=copy.deepcopy(samples['PortfolioProcessingProjectionV1']);invalid['positions']=[{'account':'denied'}]
    assert not validator('PortfolioProcessingProjectionV1').is_valid(invalid)
    negative_count += 1
    invalid=copy.deepcopy(samples['InvestmentContextReferenceV1']);del invalid['demoRunId']
    assert not validator('InvestmentContextReferenceV1').is_valid(invalid)
    negative_count += 1
    invalid=copy.deepcopy(samples['PaperPreviewProvenanceV1']);invalid['origin']='STRATEGY'
    assert not validator('PaperPreviewProvenanceV1').is_valid(invalid)
    negative_count += 1
    for field, value in [('costUpperBound', '-0.01'), ('toolAllowlist', []), ('dataClasses', [])]:
        invalid = copy.deepcopy(samples['PortfolioProcessingPreviewV1']); invalid[field] = value
        assert not validator('PortfolioProcessingPreviewV1').is_valid(invalid)
        negative_count += 1
    for field in ('reserved', 'settled'):
        invalid = copy.deepcopy(samples['InvestmentBudgetSummaryV1']); invalid[field] = '-0.01'
        assert not validator('InvestmentBudgetSummaryV1').is_valid(invalid)
        negative_count += 1
    # All bundle components exactly mirror canonical definitions; no divergent schema authority.
    bundle=json.loads((ROOT/'contracts/schemas/investment-experience-v1.schema.json').read_text('utf-8'))
    Draft202012Validator.check_schema(bundle)
    for name,schema in bundle['components']['schemas'].items():
        assert schema == document['components']['schemas'][name], 'IEF schema mirror drift: '+name
    standalone = Draft202012Validator(bundle, format_checker=contract_formats())
    for component in ('InvestmentPortfolioViewV1', 'PaperLimitPreviewV1', 'PortfolioProcessingPreviewV1'):
        standalone.validate(samples[component])
    operations=json.loads((ROOT/'contracts/ief-operations.json').read_text('utf-8'))
    for name,entry in operations.items():
        assert document['paths'][entry['path']][entry['method']]['operationId']==name
    print(f'PASS: {len(manifest)} IEF fixtures, {negative_count} rejection cases, {len(operations)} staged operations, schema mirrors (contract only; no runtime assertion)')
