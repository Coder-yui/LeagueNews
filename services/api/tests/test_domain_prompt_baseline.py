"""Prompt bodies frozen from pre-convergence commit a2efffe (no semantic edits)."""

import ast
import hashlib
import inspect
from app.methods import llm_tasks


def test_domain_prompt_bodies_match_pre_refactor_baseline():
    expected = {
        "analyze_message_content": "a851a5e3260e4f50a3ffe7a098113919aecb3f558f587455cc78d68f01b1b558",
        "classify_and_score_importance": "2cc40b5d48e86e9726ac0115c67d8d4ae1a09049fd10a44941c00773a2ab93ad",
        "aggregate_events": "ea056e110540ee9f3623426dc164154086031c541f2d66e55d7dcb2047786504",
    }
    tree = ast.parse(inspect.getsource(llm_tasks))
    methods = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    for method in methods.body:
        if isinstance(method, ast.AsyncFunctionDef) and method.name in expected:
            prompt = next(
                node.value.value
                for node in ast.walk(method)
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "prompt"
                    for target in node.targets
                )
            )
            assert hashlib.sha256(prompt.encode()).hexdigest() == expected[method.name]
