"""Generates unified openapi.json spec from Pydantic v2 contracts."""
import os
import json
from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'ha-addon')))

import contracts
from version import __version__


def generate_openapi_spec() -> dict:
    """Build a complete OpenAPI 3.1.0 specification object from Pydantic v2 models."""
    models = [
        contracts.GivTCPWriteSlot,
        contracts.GivTCPTarget,
        contracts.GivTCPBatteryMode,
        contracts.InverterTelemetry,
        contracts.OctopusRateSlot,
        contracts.LLMVetoDecision,
        contracts.HiveHotWaterState,
    ]

    schemas = {}
    for model in models:
        schemas[model.__name__] = model.model_json_schema()

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "GivEnergy Tariff Optimiser — Data Contracts & Schemas",
            "version": __version__,
            "description": "Auto-generated OpenAPI specification from Pydantic v2 data models. Single source of truth for inverter telemetry, Octopus tariffs, GivTCP REST payloads, and ChatGPT AI veto validation.",
        },
        "components": {
            "schemas": schemas
        }
    }
    return spec


def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    output_path = os.path.join(root_dir, 'openapi.json')
    
    spec = generate_openapi_spec()
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(spec, f, indent=2)
        
    print(f"✓ Generated openapi.json ({os.path.getsize(output_path)} bytes) -> {output_path}")


if __name__ == '__main__':
    main()
