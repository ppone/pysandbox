import re

from battleground_state import BattlegroundState
from constants import Outcome, TurnPhase


class GameState(object):
    """The game state, encoding players' lives and creatures.

    GameState is implemented as a mutable class - this means that it has
    methods that modify the given class, instead of returning an instance
    of a new class.

    Structure of a turn and how it relates to state:
    1) DeclareAttackersStep - during this step attackers are declared,
    one by one, via the .declare_attackers method. After this step
    the state of which creatures are attacking is kept in battleground, via
    CreatureState (an implied list).
    2) DeclareBlockersStep - during this step blockers declared (as blocking
    some attacking creature), via the .declare_blockers method. After this
    step the state of which creature blocks where, and which creature is
    not blocked is kept in battleground, via CreatureState => an implied mapping
        Map[ <attacking_creature> -> List[<blocking_creature>] ]
    3) CombatDamageStep - during this step the blockers must be ordered by
    the attacking player, via the .resolve_combat(combat_assignment) method.
    This will take as argument an explicit mapping, which will represent some
    ordering of the blocking creatures.
    """

    def __init__(self, battleground=None):
        if battleground is None:
            battleground = BattlegroundState()

        self._life1 = 20
        self._life2 = 20
        self.active_player = 0
        self.phase = TurnPhase.DeclareAttackers
        self.battleground = battleground

    def copy(self):
        """Create a deepcopy of this object."""
        result = GameState(self.battleground.copy())
        result._life1 = self._life1
        result._life2 = self._life2
        result.active_player = self.active_player
        result.phase = self.phase
        return result

    def normalize(self):
        """Normalize this instance by converting it to something hashable."""
        normalized = (self._life1, self._life2, self.active_player, self.phase)
        return (normalized, self.battleground.normalize())

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.normalize() == other.normalize()
        return NotImplemented

    def __hash__(self):
        return hash(self.normalize())

    def __repr__(self):
        """Converts to a string of the following form:
            <life0>/<life1> (<active_player>/<phase>): <battleground_state>

        E.g.:
            '20/18 (0/0): 2/3 (T), 4/6 vs 0/7'
            '20/-2 (0/2): vs 0/7'

        WARNING - this is not as strong as the .normalize() method.
        """
        return '%d/%d (%d/%d): %r' % (self._life1, self._life2,
                                      self.active_player, self.phase,
                                      self.battleground)

    _FROM_STRING_PATTERN = \
        '(?P<life1>.+)/(?P<life2>.+) ' \
        '\((?P<active_player>\d)/(?P<phase>\d)\): (?P<battleground>.*)'

    @classmethod
    def from_string(cls, string):
        match = re.match(cls._FROM_STRING_PATTERN, string)
        if not match:
            raise ValueError('Invalid string: %r' % string)
        params = match.groupdict()

        game_state = GameState()
        game_state._life1 = int(params['life1'])
        game_state._life2 = int(params['life2'])
        game_state.active_player = int(params['active_player'])
        game_state.phase = int(params['phase'])
        game_state.battleground = \
            BattlegroundState.from_string(params['battleground'])
        return game_state

    @property
    def next_to_act(self):
        if self.phase == TurnPhase.DeclareBlockers:
            return self.defending_player
        else:
            return self.attacking_player

    @property
    def attacking_player(self):
        return self.active_player

    @property
    def defending_player(self):
        return 1 - self.active_player

    @property
    def attacking_player_creatures(self):
        return self.battleground.get_creatures(self.attacking_player)

    @property
    def defending_player_creatures(self):
        return self.battleground.get_creatures(self.defending_player)

    @property
    def is_over(self):
        return self._life1 <= 0 or self._life2 <= 0

    @property
    def outcome(self):
        """Return the outcome of the current state (win, loss or draw).
        Will raise an error if the game is not yet over.
        """
        if not self.is_over:
            raise ValueError('The game is not over yet.')
        # Very unlikely, but nice to cover this corner case as well.
        if self._life1 <= 0 and self._life2 <= 0:
            return Outcome.Draw
        # Which player is dead?
        dead = 0 if self._life1 <= 0 else 1
        if dead == self.next_to_act:
            return Outcome.Loss
        else:
            return Outcome.Win

    def untap(self):
        """Untap for active player."""
        for creature in self.battleground.get_creatures(self.active_player):
            creature.untap()

    ### CombatPhase-related ###

    def _expect_step(self, expected_phase_or_step):
        if self.phase != expected_phase_or_step:
            raise ValueError('Invalid turn phase or step')

    def declare_attackers(self, attacking_creature_uids):
        self._expect_step(TurnPhase.DeclareAttackers)
        if not self.is_valid_attack(attacking_creature_uids):
            raise ValueError('Invalid attack')

        if attacking_creature_uids:
            for uid in attacking_creature_uids:
                creature_state = self.battleground[uid]
                creature_state.attack()
                creature_state.tap()
            self.phase = TurnPhase.DeclareBlockers
        else:
            self.end_turn()

    def is_valid_attack(self, attacking_creature_uids):
        for uid in attacking_creature_uids:
            creature_state = self.battleground[uid]
            if creature_state.tapped:
                return False
            if creature_state.controlling_player != self.attacking_player:
                return False
        return True

    def declare_blockers(self, blocking_assignment=None):
        """
        Args:
            blocking_assignment: a map of <blocker_uid> -> <blocker_uid>
        """
        self._expect_step(TurnPhase.DeclareBlockers)

        if blocking_assignment is None:
            blocking_assignment = {}
        if not self.is_valid_block(blocking_assignment):
            raise ValueError('Invalid blocking assignment')

        for blocker_uid, blocked_uid in blocking_assignment.items():
            blocker = self.battleground[blocker_uid]
            blocker.block(blocked_uid)

        self.phase = TurnPhase.CombatStep

    def is_valid_block(self, blocking_assignment):
        for blocker_uid, blocked_uid in blocking_assignment.items():
            blocker = self.battleground[blocker_uid]
            blocked = self.battleground[blocked_uid]
            # Tapped creatures cannot block.
            if blocker.tapped:
                return False
            # Only creatures controlled by defending player can block.
            if blocker.controlling_player != self.defending_player:
                return False
            # Can only block an attacking creature.
            if not blocked.attacking:
                return False
        return True

    def resolve_combat(self, combat_assignment=None):
        """Resolve combat, given the specified CombatAssignment, if any.

        If combat_assignment is missing, an arbitrary (undefined)
        CombatAssignment will be used instead.
        """
        self._expect_step(TurnPhase.CombatStep)

        current_combat_assignment = self.battleground.get_combat_assignment()
        if combat_assignment is None:
            combat_assignment = current_combat_assignment
        else:
            # Is combat_assignment a correct reordering or all blockers?
            if not combat_assignment.is_reorder_of(current_combat_assignment):
                raise ValueError('Invalid combat_assignment argument: %r '
                                 'is not a reorder of %r' %
                                 (combat_assignment, current_combat_assignment))

        for attacker_uid, blocker_uids in combat_assignment.items():
            if blocker_uids:
                self._resolve_blocked_attacker(attacker_uid, blocker_uids)
            else:
                self._resolve_unblocked_attacker(attacker_uid)
        self.end_turn()

    def _resolve_unblocked_attacker(self, attacker_uid):
        attacking_creature = self.battleground[attacker_uid]
        # Deal damage to defending player.
        if self.defending_player == 0:
            self._life1 -= attacking_creature.power
        else:
            self._life2 -= attacking_creature.power
        attacking_creature.remove_from_combat()

    def _resolve_blocked_attacker(self, attacker_uid, blocker_uids):
        attacking_creature = self.battleground[attacker_uid]

        blockers = [self.battleground[uid] for uid in blocker_uids]
        blockers_total_damage = sum(blocker.power for blocker in blockers)

        # Destroy blockers, then deal remaining damage (if any) to defending
        # player.
        attacking_damage = attacking_creature.power
        for uid, blocker in zip(blocker_uids, blockers):
            if attacking_damage >= blocker.toughness:
                attacking_damage -= blocker.toughness
                # Blocker has died, remove it from current state.
                self.battleground.remove_creature(uid)
            else:
                break

        # Destroy attacker, if needed.
        if blockers_total_damage >= attacking_creature.toughness:
            self.battleground.remove_creature(attacker_uid)

        attacking_creature.remove_from_combat()
        for blocker in blockers:
            blocker.remove_from_combat()

    def end_turn(self):
        """End the current turn, and pass turn to the other player."""
        self.active_player = 1 - self.active_player
        self.phase = TurnPhase.DeclareAttackers
        self.untap()
