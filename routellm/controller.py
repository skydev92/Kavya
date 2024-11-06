import warnings
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

import pandas as pd
from litellm import acompletion, completion
from tqdm import tqdm

import os
import json

from models import ChatCompletionRequest
from routellm.routers.routers import ROUTER_CLS

# Default config for routers augmented using golden label data from GPT-4.
# This is exactly the same as config.example.yaml.
GPT_4_AUGMENTED_CONFIG = {
    "sw_ranking": {
        "arena_battle_datasets": [
            "lmsys/lmsys-arena-human-preference-55k",
            "routellm/gpt4_judge_battles",
        ],
        "arena_embedding_datasets": [
            "routellm/arena_battles_embeddings",
            "routellm/gpt4_judge_battles_embeddings",
        ],
    },
    "causal_llm": {"checkpoint_path": "routellm/causal_llm_gpt4_augmented"},
    "bert": {"checkpoint_path": "routellm/bert_gpt4_augmented"},
    "mf": {"checkpoint_path": "routellm/mf_gpt4_augmented"},
}


class RoutingError(Exception):
    pass


@dataclass
class ModelPair:
    strong: str
    weak: str


class Controller:
    def __init__(
        self,
        routers: list[str],
        strong_model: str,
        weak_model: str,
        config: Optional[dict[str, dict[str, Any]]] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        progress_bar: bool = False,
        suppress_warnings: bool = False,
    ):
        self.model_pair = ModelPair(strong=strong_model, weak=weak_model)
        self.routers = {}
        self.api_base = api_base
        self.api_key = api_key
        self.model_counts = defaultdict(lambda: defaultdict(int))
        self.progress_bar = progress_bar

        if config is None:
            config = GPT_4_AUGMENTED_CONFIG

        router_pbar = None
        if progress_bar:
            router_pbar = tqdm(routers)
            tqdm.pandas()

        for router in routers:
            if router_pbar is not None:
                router_pbar.set_description(f"Loading {router}")
            self.routers[router] = ROUTER_CLS[router](**config.get(router, {}))

        # Some Python magic to match the OpenAI Python SDK
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self.completion, acreate=self.acompletion
            )
        )
        self.suppress_warnings = suppress_warnings

        self.predefined_prompts = self.load_predefined_prompts()

    def load_predefined_prompts(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, 'predefined_prompts.json')
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except:
            return {}

    def check_predefined_prompt(self, message):
        for key, value in self.predefined_prompts.items():
            if key in message:
                return value
        return None

    def _validate_router_threshold(
        self, router: Optional[str], threshold: Optional[float]
    ):
        if router not in self.routers:
            raise RoutingError(f"Router {router} not found.")
        if threshold is None or threshold < 0 or threshold > 1:
            raise RoutingError(f"Threshold {threshold} must be between 0 and 1.")

    def _parse_model_name(self, model: str):
        if model.startswith("router-"):
            parts = model.split("-")
            if len(parts) != 3:
                raise RoutingError(
                    f"Invalid model name {model}. Expected format: router-<router>-<threshold>"
                )
            router = parts[1]
            try:
                threshold = float(parts[2])
            except ValueError as e:
                raise RoutingError(f"Threshold {threshold} must be a float.") from e
            return router, threshold
        else:
            return None, None

    def _get_routed_model_for_completion(
        self, messages: list, router: str, threshold: float
    ):
        prompt = messages[-1]["content"]
        routed_model = self.routers[router].route(prompt, threshold, self.model_pair)

        self.model_counts[routed_model] += 1

        return routed_model

    def batch_calculate_win_rate(
        self,
        prompts: pd.Series,
        router: str,
        threshold: float,
    ):
        return self.routers[router].batch_calculate_win_rate(
            prompts, threshold, self.model_pair
        )

    def route(self, prompt: str, router: str, threshold: float):
        self._validate_router_threshold(router, threshold)

        return self.routers[router].route(prompt, threshold, self.model_pair)

    def completion(
        self,
        *,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        **kwargs,
    ):
        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": predefined_answer}
                        }
                    ],
                    "model": "predefined_prompt"
                }

        if "model" in kwargs:
            router, threshold = self._parse_model_name(kwargs["model"])

        if router and threshold:
            self._validate_router_threshold(router, threshold)
            kwargs["model"] = self._get_routed_model_for_completion(
                kwargs["messages"], router, threshold
            )
        elif "model" not in kwargs:
            raise RoutingError("No model specified and router/threshold not provided.")

        if self.suppress_warnings:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                return completion(api_base=self.api_base, api_key=self.api_key, **kwargs)
        else:
            return completion(api_base=self.api_base, api_key=self.api_key, **kwargs)

    async def acompletion(
        self,
        *,
        router: Optional[str] = None,
        threshold: Optional[float] = None,
        **kwargs,
    ):
        if "messages" in kwargs:
            last_message = kwargs["messages"][-1]["content"]
            predefined_answer = self.check_predefined_prompt(last_message)
            if predefined_answer:
                return {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": predefined_answer}
                        }
                    ],
                    "model": "predefined_prompt"
                }

        if "model" in kwargs:
            parsed_router, parsed_threshold = self._parse_model_name(kwargs["model"])
            router = router or parsed_router
            threshold = threshold or parsed_threshold

        if router and threshold:
            self._validate_router_threshold(router, threshold)
            kwargs["model"] = self._get_routed_model_for_completion(
                kwargs["messages"], router, threshold
            )
        elif "model" not in kwargs:
            raise RoutingError("No model specified and router/threshold not provided.")

        if self.suppress_warnings:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                return await acompletion(api_base=self.api_base, api_key=self.api_key, **kwargs)
        else:
            return await acompletion(api_base=self.api_base, api_key=self.api_key, **kwargs)
        
class Controllers:
    def __init__(self, **kwargs):
        self.controllers = {}
        self.create_controller("default", **kwargs)

    def create_controller(self, id, **kwargs):
        # getting kwargs
        routers=kwargs.get('routers', None)
        config=kwargs.get('config', None)
        strong_model=kwargs.get('strong_model', None)
        weak_model=kwargs.get('weak_model', None)
        api_base=kwargs.get('api_base', None)
        api_key=kwargs.get('api_key', None)
        progress_bar=kwargs.get('progress_bar', None)

        # initializing controller
        controller = Controller(
            routers=routers,
            config=config,
            strong_model=strong_model,
            weak_model=weak_model,
            api_base=api_base,
            api_key=api_key,
            progress_bar=progress_bar
        )

        # storing controller
        self.controllers[id] = controller

        return controller
    
    """
    Call an async method on a controller with the given id.

    Parameters
    ----------
    request : ChatCompletionRequest
        The request to pass to the method.
    id : str
        The id of the controller to call.
    amethod_name : str
        The name of the async method to call.

    Returns
    -------
    The response from the async method.
    """
    async def response(self, request: ChatCompletionRequest, id, amethod_name):
        return await self.controllers[id].__getattribute__(amethod_name)(
            **request.model_dump(exclude_none=True),
        )

    # METHODS TO IMITATE DICT INTERFACE

    def __repr__(self):
        return repr(self.controllers)

    def __del__(self):
        for controller in self.controllers.values():
            del controller

    def __getattr__(self, name):
        return self.controllers[name]

    def __contains__(self, id):
        return id in self.controllers

    def __getitem__(self, id):
        return self.controllers[id]

    def __setitem__(self, id, controller):
        self.controllers[id] = controller

    def __delitem__(self, id):
        del self.controllers[id]

    def __iter__(self):
        return iter(self.controllers)

    def __len__(self):
        return len(self.controllers)

    def __repr__(self):
        return repr(self.controllers)

    def __str__(self):
        return str(self.controllers)