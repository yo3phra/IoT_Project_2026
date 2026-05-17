"""
Challenge manager - generates and validates liveness challenges.
Implements challenge-response protocol for anti-spoofing.
"""

import random
import time
from typing import List, Optional, Dict
from enum import Enum
from config import get_config
from logger import logger_challenge
from errors import LivenessError
from typing import Tuple


class ChallengeType(Enum):
    """Types of liveness challenges."""
    HEAD_TURN_LEFT = "turn_head_left"
    HEAD_TURN_RIGHT = "turn_head_right"
    BLINK = "blink_twice"
    MOUTH_OPEN = "open_mouth"
    RANDOM_SEQUENCE = "random_sequence"


class Challenge:
    """Represents a single liveness challenge."""

    def __init__(self, challenge_type: ChallengeType, challenge_id: str):
        """
        Args:
            challenge_type: Type of challenge to present
            challenge_id: Unique identifier for this challenge
        """
        self.type = challenge_type
        self.id = challenge_id
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.is_passed: Optional[bool] = None
        self.response_data: Dict = {}

    @property
    def age_sec(self) -> float:
        """Age of challenge in seconds."""
        return time.time() - self.created_at

    @property
    def is_timed_out(self) -> bool:
        """Check if challenge has timed out."""
        config = get_config().liveness
        return self.age_sec > config.timeout_per_challenge_sec

    @property
    def human_description(self) -> str:
        """Human-readable challenge description."""
        descriptions = {
            ChallengeType.HEAD_TURN_LEFT: "Turn your head to the LEFT",
            ChallengeType.HEAD_TURN_RIGHT: "Turn your head to the RIGHT",
            ChallengeType.BLINK: "Blink twice",
            ChallengeType.MOUTH_OPEN: "Open your mouth",
            ChallengeType.RANDOM_SEQUENCE: "Follow the instructions",
        }
        return descriptions.get(self.type, "Complete challenge")

    def complete(self, passed: bool, response_data: Dict = None):
        """Mark challenge as complete."""
        self.completed_at = time.time()
        self.is_passed = passed
        if response_data:
            self.response_data = response_data

    def __repr__(self):
        return f"Challenge(type={self.type.value}, passed={self.is_passed}, timed_out={self.is_timed_out})"


class ChallengeSequence:
    """Manages a sequence of challenges for one liveness check."""

    def __init__(self, sequence_id: str, num_challenges: int = 1):
        """
        Args:
            sequence_id: Unique ID for this sequence
            num_challenges: Number of challenges in sequence
        """
        self.sequence_id = sequence_id
        self.challenges: List[Challenge] = []
        self.current_challenge_idx = 0
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.passed: Optional[bool] = None

        # Generate challenges
        self._generate_challenges(num_challenges)

    def _generate_challenges(self, num: int):
        """Generate random non-repeating challenges."""
        challenge_types = [
            ChallengeType.HEAD_TURN_LEFT,
            ChallengeType.HEAD_TURN_RIGHT,
            ChallengeType.MOUTH_OPEN
        ]

        selected = random.sample(challenge_types, min(num, len(challenge_types)))

        for i, challenge_type in enumerate(selected):
            challenge_id = f"{self.sequence_id}_ch{i}"
            challenge = Challenge(challenge_type, challenge_id)
            self.challenges.append(challenge)

        logger_challenge.debug(
            f"Generated challenge sequence: {[c.type.value for c in self.challenges]}"
        )

    @property
    def current_challenge(self) -> Optional[Challenge]:
        """Get current challenge to present."""
        if self.current_challenge_idx < len(self.challenges):
            return self.challenges[self.current_challenge_idx]
        return None

    @property
    def progress(self) -> Tuple[int, int]:
        """Get progress as (completed, total)."""
        return self.current_challenge_idx, len(self.challenges)

    @property
    def is_complete(self) -> bool:
        """Check if sequence is complete."""
        return self.current_challenge_idx >= len(self.challenges)

    def advance(self) -> bool:
        """Move to next challenge. Returns False if sequence complete."""
        if self.is_complete:
            return False

        self.current_challenge_idx += 1

        if self.is_complete:
            self._finalize()

        return not self.is_complete

    def complete_current(self, passed: bool, response_data: Dict = None):
        """Mark current challenge as complete and move to next."""
        if current := self.current_challenge:
            current.complete(passed, response_data)

            if passed:
                logger_challenge.debug(f"Challenge passed: {current.type.value}")
            else:
                logger_challenge.debug(f"Challenge failed: {current.type.value}")

    def _finalize(self):
        """Finalize the sequence."""
        self.completed_at = time.time()

        # Sequence passes if all challenges passed
        self.passed = all(c.is_passed for c in self.challenges if c.is_passed is not None)

        logger_challenge.info(
            f"Challenge sequence complete: passed={self.passed}, "
            f"challenges={[c.is_passed for c in self.challenges]}"
        )

    @property
    def age_sec(self) -> float:
        """Age of sequence in seconds."""
        return time.time() - self.created_at

    @property
    def is_timed_out(self) -> bool:
        """Check if sequence has timed out (total timeout)."""
        config = get_config().auth_controller
        return self.age_sec > config.total_timeout_sec

    def __repr__(self):
        completed, total = self.progress
        return f"ChallengeSequence(id={self.sequence_id}, {completed}/{total}, passed={self.passed})"


class ChallengeManager:
    """Manages liveness challenge generation and validation."""

    def __init__(self):
        """Initialize challenge manager."""
        self.config = get_config().liveness
        self.sequences: Dict[str, ChallengeSequence] = {}

    def create_sequence(self, user_id: str, num_challenges: int = None) -> ChallengeSequence:
        """
        Create a new challenge sequence for user.

        Args:
            user_id: User undergoingliveness check
            num_challenges: Number of challenges to present (defaults to config value)

        Returns:
            ChallengeSequence object
        """
        if num_challenges is None:
            num_challenges = self.config.num_challenges

        sequence_id = f"seq_{user_id}_{int(time.time() * 1000)}"
        sequence = ChallengeSequence(sequence_id, num_challenges)
        self.sequences[sequence_id] = sequence

        logger_challenge.info(f"Created challenge sequence: {sequence}")

        return sequence

    def get_sequence(self, sequence_id: str) -> Optional[ChallengeSequence]:
        """Retrieve active sequence."""
        return self.sequences.get(sequence_id)

    def validate_head_turn_left(
        self,
        sequence_id: str,
        head_pose_yaw: float
    ) -> bool:
        """
        Validate "turn head left" challenge.

        Args:
            sequence_id: Challenge sequence ID
            head_pose_yaw: Head yaw angle from detector

        Returns:
            True if challenge passed
        """
        sequence = self.get_sequence(sequence_id)
        if not sequence or sequence.current_challenge is None:
            return False

        challenge = sequence.current_challenge
        if challenge.type != ChallengeType.HEAD_TURN_LEFT:
            return False

        # Check if head turned left sufficiently
        required_deg = self.config.required_head_turn_degrees
        passed = head_pose_yaw < -required_deg

        challenge.complete(passed, {"head_yaw": head_pose_yaw})
        sequence.complete_current(passed, {"head_yaw": head_pose_yaw})

        return passed

    def validate_head_turn_right(
        self,
        sequence_id: str,
        head_pose_yaw: float
    ) -> bool:
        """
        Validate "turn head right" challenge.

        Args:
            sequence_id: Challenge sequence ID
            head_pose_yaw: Head yaw angle from detector

        Returns:
            True if challenge passed
        """
        sequence = self.get_sequence(sequence_id)
        if not sequence or sequence.current_challenge is None:
            return False

        challenge = sequence.current_challenge
        if challenge.type != ChallengeType.HEAD_TURN_RIGHT:
            return False

        required_deg = self.config.required_head_turn_degrees
        passed = head_pose_yaw > required_deg

        challenge.complete(passed, {"head_yaw": head_pose_yaw})
        sequence.complete_current(passed, {"head_yaw": head_pose_yaw})

        return passed

    def validate_blink(
        self,
        sequence_id: str,
        blink_count: int
    ) -> bool:
        """
        Validate "blink N times" challenge.

        Args:
            sequence_id: Challenge sequence ID
            blink_count: Number of blinks detected

        Returns:
            True if challenge passed
        """
        sequence = self.get_sequence(sequence_id)
        if not sequence or sequence.current_challenge is None:
            return False

        challenge = sequence.current_challenge
        if challenge.type != ChallengeType.BLINK:
            return False

        required_blinks = self.config.required_blink_count
        passed = blink_count >= required_blinks

        challenge.complete(passed, {"blink_count": blink_count})
        sequence.complete_current(passed, {"blink_count": blink_count})

        return passed

    def validate_mouth_open(self, sequence_id: str, is_open: bool) -> bool:
        """
        Validate "open mouth" challenge.

        Args:
            sequence_id: Challenge sequence ID
            is_open: Whether mouth detected as open

        Returns:
            True if challenge passed
        """
        sequence = self.get_sequence(sequence_id)
        if not sequence or sequence.current_challenge is None:
            return False

        challenge = sequence.current_challenge
        if challenge.type != ChallengeType.MOUTH_OPEN:
            return False

        passed = is_open

        challenge.complete(passed, {"mouth_open": is_open})
        sequence.complete_current(passed, {"mouth_open": is_open})

        return passed

    def is_sequence_passed(self, sequence_id: str) -> Optional[bool]:
        """Check if sequence fully passed."""
        sequence = self.get_sequence(sequence_id)
        if not sequence:
            return None
        return sequence.passed

    def cleanup_old_sequences(self, max_age_sec: int = 300):
        """Remove old/timed-out sequences to prevent memory leak."""
        current_time = time.time()
        to_remove = []

        for seq_id, sequence in self.sequences.items():
            age = current_time - sequence.created_at
            if age > max_age_sec or (sequence.completed_at and age > 60):
                to_remove.append(seq_id)

        for seq_id in to_remove:
            del self.sequences[seq_id]

        if to_remove:
            logger_challenge.debug(f"Cleaned up {len(to_remove)} old sequences")


