"""E7: owner line observation of unassigned slots and pending set_line work."""

import json
import unittest

from organizer.rift_era.engine.catalog import Catalog
from organizer.rift_era.engine.construction import WorkOrder
from organizer.rift_era.engine.economy import opening_economy
from organizer.rift_era.engine.initialization import initialize
from organizer.rift_era.engine.own_observation import owned_entities
from organizer.rift_era.engine.world import generate_candidate


class LineObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = Catalog()
        cls.state = opening_economy(catalog, initialize(catalog, generate_candidate(1000)), total_ticks=10,
                                    commerce=True).state

    def rows(self, owner='n0'):
        return {row['id']: row for row in owned_entities(self.state, owner)['lines']}

    def test_installed_recipe_and_no_pending_by_default(self):
        rows = self.rows()
        lid, line = next((lid, l) for lid, l in sorted(self.state.initial.lines.items())
                         if l.owner == 'n0' and l.recipe.id != '__idle__')
        self.assertEqual(rows[lid]['recipe'], line.recipe.id)
        self.assertTrue(all(r['pending_recipe'] is None and r['pending_mode'] is None for r in rows.values()))

    def test_unassigned_slot_reports_null_recipe(self):
        from organizer.rift_era.engine.industry_commands import IndustryCommands
        fid, facility = next((fid, f) for fid, f in sorted(self.state.initial.facilities.items())
                             if f.owner == 'n0' and len(f.line_ids) < self.state.catalog.profiles[f.profile]['lines'])
        commands = IndustryCommands.__new__(IndustryCommands)
        commands.state = self.state
        grown = commands._new_lines(facility)
        self.state.initial.facilities[fid] = grown
        new = [lid for lid in grown.line_ids if lid not in facility.line_ids]
        rows = self.rows()
        for lid in new:
            self.assertIsNone(rows[lid]['recipe'])
            self.assertEqual(rows[lid]['slot_use'], 'unassigned')
            self.assertIsNone(rows[lid]['pending_recipe'])

    def test_pending_set_line_reports_target_recipe(self):
        lid = next(lid for lid, l in sorted(self.state.initial.lines.items()) if l.owner == 'n0')
        installed = self.state.initial.lines[lid].recipe.id
        job = WorkOrder('work:test:e7', '9', 'n0', self.state.initial.lines[lid].hub, 'set_line', lid, 20, {},
                        payload={'recipe': 'P075', 'mode': 'general', 'options': {}})
        self.state.work.jobs[job.id] = job
        try:
            rows = self.rows()
            self.assertEqual(rows[lid]['pending_recipe'], 'P075')
            self.assertEqual(rows[lid]['pending_mode'], 'general')
            self.assertEqual(rows[lid]['recipe'], None if installed == '__idle__' else installed)
            # Another seat never sees this nation's pending work.
            self.assertTrue(all(r['pending_recipe'] is None for r in self.rows('n1').values()))
            json.dumps(owned_entities(self.state, 'n0'))
        finally:
            del self.state.work.jobs[job.id]


if __name__ == '__main__':
    unittest.main()
