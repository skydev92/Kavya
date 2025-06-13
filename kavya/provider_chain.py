import logging
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ProviderChain:
    """Single, stateful source of truth for provider management during a request."""

    providers: List[str]
    failed: List[str] = field(default_factory=list)
    current_index: int = field(default=0, init=False)

    def next(self) -> Optional[str]:
        """Returns the next available provider that has not failed."""
        logging.debug(
            f"FALLBACK: ProviderChain.next() called. Current state: index={self.current_index}, failed={self.failed}"
        )

        while self.current_index < len(self.providers):
            provider = self.providers[self.current_index]
            self.current_index += 1

            if provider not in self.failed:
                logging.info(
                    f"FALLBACK: Next provider in chain: {provider} (index was {self.current_index-1})"
                )
                return provider
            else:
                logging.debug(f"FALLBACK: Skipping failed provider: {provider}")

        logging.warning(f"FALLBACK: No more providers available. Failed: {self.failed}")
        return None

    def mark_failed(self, model: str) -> None:
        """Marks a provider as failed for the rest of this request."""
        if model not in self.failed:
            self.failed.append(model)
            logging.info(
                f"FALLBACK: Marked provider as failed: {model}. Total failed: {len(self.failed)}"
            )

    def has_more_providers(self) -> bool:
        """Returns True if there are more providers to try."""
        return any(
            p for p in self.providers[self.current_index :] if p not in self.failed
        )

    def get_remaining_count(self) -> int:
        """Returns the number of remaining providers."""
        return len(
            [p for p in self.providers[self.current_index :] if p not in self.failed]
        )

    def __repr__(self) -> str:
        return f"ProviderChain(providers={self.providers}, failed={self.failed}, current_index={self.current_index})"

    def create_fresh_copy(self):
        """Create a copy of this ProviderChain with reset current_index but preserved failed state."""
        fresh_copy = ProviderChain(providers=self.providers.copy())
        fresh_copy.failed = self.failed.copy()  # Preserve failed providers
        fresh_copy.current_index = 0  # Reset to start from beginning
        logging.debug(
            f"FALLBACK: Created fresh copy. Original failed state: {self.failed}, Original index: {self.current_index}"
        )
        logging.debug(
            f"FALLBACK: Fresh copy failed state: {fresh_copy.failed}, Fresh copy index: {fresh_copy.current_index}"
        )
        logging.debug(
            f"FALLBACK: Created fresh copy. Original: {self}, Fresh copy: {fresh_copy}"
        )
        return fresh_copy
