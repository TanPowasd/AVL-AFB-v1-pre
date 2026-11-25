"""03 G07 combined-arms / G12 joint-command doctrine: fire x1.2 (x1.1), shared q .92, 10-tick freeze."""
import unittest
from types import SimpleNamespace

from organizer.rift_era.tests import test_transport_operations as fixture_source
from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.combat import TargetContact, Unit, resolve
from organizer.rift_era.engine.diplomacy import Diplomacy
from organizer.rift_era.engine.military_doctrine import eligible, share_tracks, update_joint_command
from organizer.rift_era.engine.numeric import Q


class FireMultiplierTests(unittest.TestCase):
    def test_multiplier_scales_only_the_marked_shooter(self):
        catalog = Catalog()
        d = Diplomacy(frozenset({'A', 'B'}))
        d.phase1(0, declarations=(('A', 'B'),))
        a = Unit('a', 'A', 'infantry', 'r', 100000, 1000)
        b = Unit('b', 'B', 'infantry', 'r', 100000, 1000)
        contacts = {'a': (TargetContact('b', Q(1), 0),), 'b': (TargetContact('a', Q(1), 0),)}
        supply = {'a': {'ammunition': 500}, 'b': {'ammunition': 500}}
        base = resolve(catalog, d, {'a': a, 'b': b}, contacts, supply, {'r': 'plain'})
        boosted = resolve(catalog, d, {'a': a, 'b': b}, contacts, supply, {'r': 'plain'},
                          fire_multiplier={'a': Q(6, 5) * Q(11, 10)})
        raw = {s.source: s.raw_damage_mU for s in base.shots}
        raw2 = {s.source: s.raw_damage_mU for s in boosted.shots}
        self.assertEqual(raw2['a'], raw['a'] * Q(66, 50))
        self.assertEqual(raw2['b'], raw['b'])


class DoctrineStateTests(unittest.TestCase):
    setUpClass = classmethod(fixture_source.CargoCommandTests.setUpClass.__func__)
    fixture = fixture_source.CargoCommandTests.fixture

    def setup(self):
        state = self.fixture().state
        rows = [f for f in state.formations.rows.values() if f.owner == 'n0']
        region = rows[0].region
        group = [f for f in rows if f.region == region]
        return state, group, region

    def test_conditions_tech_slot_types_network_and_g12_freeze(self):
        state, group, region = self.setup()
        target = group[0]
        connected = lambda owner: {region} if owner == 'n0' else set()
        self.assertEqual(eligible(state, connected), {})
        state.research['n0'].known.add('G07')
        self.assertEqual(eligible(state, connected), {})  # no radio/sensor slot
        target.slot_a = 'sensor'
        self.assertEqual(eligible(state, connected), {target.id: Q(6, 5)})
        self.assertEqual(eligible(state, lambda owner: set()), {})  # not networked
        state.research['n0'].known.add('G12')
        update_joint_command(state, 5, connected)
        self.assertEqual(eligible(state, connected), {target.id: Q(6, 5) * Q(11, 10)})
        update_joint_command(state, 6, lambda owner: set())
        update_joint_command(state, 7, connected)
        self.assertEqual(eligible(state, connected), {target.id: Q(6, 5)})
        update_joint_command(state, 16, connected)
        self.assertEqual(eligible(state, connected), {target.id: Q(6, 5) * Q(11, 10)})
        target.refitting = True
        self.assertEqual(eligible(state, connected), {})

    def test_shared_track_quality_is_92_percent_of_best_friendly(self):
        state, group, region = self.setup()
        mine, friend = group[0], group[1] if len(group) > 1 else None
        self.assertIsNotNone(friend)
        u1, u2 = mine.unit_ids[0], friend.unit_ids[0]
        contacts = {u1: (TargetContact('enemy1', Q(1, 2), 0),),
                    u2: (TargetContact('enemy1', Q(9, 10), 0), TargetContact('enemy2', Q(4, 5), 1))}
        shared = share_tracks(state, dict(contacts), [mine.id])
        got = {c.target: c for c in shared[u1]}
        self.assertEqual(got['enemy1'].quality, Q(92, 100) * Q(9, 10))
        self.assertEqual(got['enemy2'].quality, Q(92, 100) * Q(4, 5))
        self.assertEqual(got['enemy2'].hops, 1)
        # A friend's weaker track never lowers one's own better track.
        contacts[u1] = (TargetContact('enemy1', Q(19, 20), 0),)
        got = {c.target: c for c in share_tracks(state, dict(contacts), [mine.id])[u1]}
        self.assertEqual(got['enemy1'].quality, Q(19, 20))
        # Formations without the doctrine keep their own contacts unchanged.
        self.assertEqual(share_tracks(state, dict(contacts), [mine.id])[u2], contacts[u2])


if __name__ == '__main__':
    unittest.main()


class OccupationOperatingFactorTests(unittest.TestCase):
    """01: enemy-occupied, wholly enemy-owned facilities run at most 60% (military administration)."""
    setUpClass = classmethod(fixture_source.CargoCommandTests.setUpClass.__func__)
    fixture = fixture_source.CargoCommandTests.fixture

    def test_occupied_line_and_mine_capacity_capped_at_60_percent(self):
        from organizer.rift_era.engine.economy import Economy
        state = self.fixture().state
        economy = Economy(state)
        lid, line = next((l, x) for l, x in sorted(state.initial.lines.items()) if x.owner == 'n1')
        fid = lid.rsplit('/line', 1)[0]
        mid = next(m for m, mine in sorted(state.initial.mines.items()) if mine.owner == 'n1')
        self.assertEqual(economy._occupation_factor(fid), 1)
        for aid in (fid, mid):
            state.authority.regions[state.initial.map.hubs[state.initial.assets[aid].hub].region].controller = 'n0'
        # Occupied but not at war: no military administration cap.
        self.assertEqual(economy._occupation_factor(fid), 1)
        state.initial.diplomacy.phase1(0, declarations=(('n0', 'n1'),))
        self.assertEqual(economy._occupation_factor(fid), Q(3, 5))
        self.assertEqual(economy._occupation_factor(mid), Q(3, 5))
        # A neutral third party's share keeps the asset outside the cap.
        rights = state.authority.assets[fid]
        shares = dict(rights.owner_shares)
        state.authority.assets[fid].owner_shares = {**{k: v - 1 for k, v in shares.items()}, 'n2': 1} if shares else {'n2': 1}
        self.assertEqual(economy._occupation_factor(fid), 1)
