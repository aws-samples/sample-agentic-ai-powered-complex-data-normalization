# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Shared pytest fixtures and import shims for the test suite.

classifier_agent imports strands at module load. That SDK isn't
needed to exercise the security logic (sanitization, schema validation,
web_grounded derivation, fallback), so we stub it before import. This keeps
the tests fast, offline, and free of AWS/Bedrock calls.
"""
import os
import sys
import types

LAMBDA_DIR = os.path.join(os.path.dirname(__file__), '..', 'lambda_functions')
sys.path.insert(0, os.path.abspath(LAMBDA_DIR))


def _install_strands_stubs():
    """Register minimal stand-ins for strands so imports succeed."""
    if 'strands' not in sys.modules:
        strands = types.ModuleType('strands')

        class _Agent:  # minimal Agent stand-in
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

            def __call__(self, *args, **kwargs):
                return ''

        strands.Agent = _Agent
        def _tool(func=None, **kwargs):
            """@tool / @tool(name=..., description=...) passthrough."""
            def wrap(f):
                f.tool_name = kwargs.get('name', f.__name__)
                f.tool_description = kwargs.get('description', f.__doc__)
                return f
            return wrap(func) if func is not None else wrap

        strands.tool = _tool
        sys.modules['strands'] = strands

        models = types.ModuleType('strands.models')

        class _BedrockModel:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        models.BedrockModel = _BedrockModel
        sys.modules['strands.models'] = models



_install_strands_stubs()
