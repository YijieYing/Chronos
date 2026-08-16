from unittest import TestCase

from chronos.agent.meaning import (
    Content,
    Duration,
    DurationKind,
    Event,
    Field,
    Gap,
    GapReason,
    Item,
    Kind,
    Origin,
    Period,
    Provenance,
    Reference,
    Relation,
    RelationKind,
    Request,
    RequestKind,
    Residue,
    ResidueReason,
    ResidueStatus,
    Snapshot,
    Span,
    Time,
    TimeKind,
)


class MeaningTest(TestCase):
    def test_time_distinguishes_absent_symbolic_flexible_relative_and_unresolved(self) -> None:
        self.assertEqual(Time(TimeKind.NONE).type, TimeKind.NONE)
        self.assertEqual(Time(TimeKind.PERIOD, period=Period.AFTERNOON).period, Period.AFTERNOON)
        self.assertEqual(Time(TimeKind.FLEXIBLE, period=Period.EVENING).type, TimeKind.FLEXIBLE)
        self.assertEqual(Time(TimeKind.RELATIVE, relation_id="after-a").relation_id, "after-a")
        self.assertEqual(Time(TimeKind.UNRESOLVED, text="晚些时候").text, "晚些时候")
        with self.assertRaisesRegex(ValueError, "none"):
            Time(TimeKind.NONE, text="没有说")

    def test_request_is_semantic_crud_not_an_executable_operation(self) -> None:
        target = Reference("task", "research")
        edit = Request(RequestKind.EDIT, target, (Field.TIME, Field.RELATIONS))
        self.assertEqual(edit.fields, (Field.TIME, Field.RELATIONS))
        with self.assertRaisesRegex(ValueError, "target"):
            Request(RequestKind.EDIT, fields=(Field.TIME,))

    def test_combined_items_form_one_event_without_shared_duration_or_merge(self) -> None:
        a = Content("item-a", Span(5, 6), "A")
        b = Content("item-b", Span(9, 10), "B")
        event = Event(
            id="event-ab",
            item_ids=("item-a", "item-b"),
            content=(a, b),
            kind=Kind.TASK,
            request=Request(RequestKind.ADD),
            time=Time(TimeKind.NONE),
            duration=Duration(DurationKind.EXACT, minutes=60),
            provenance=(
                Provenance(
                    Origin.CLARIFICATION,
                    ("item-a", "item-b"),
                    (Span(0, 15),),
                ),
            ),
        )

        self.assertEqual(tuple(item.text for item in event.content), ("A", "B"))
        self.assertEqual(event.duration.minutes, 60)
        self.assertEqual(event.relations, ())

    def test_relative_time_must_link_to_a_relation(self) -> None:
        content = Content("item-report", Span(0, 2), "报告")
        relation = Relation(
            "after-research",
            RelationKind.AFTER,
            Reference("task", "research"),
        )
        event = Event(
            id="event-report",
            item_ids=("item-report",),
            content=(content,),
            kind=Kind.TASK,
            request=Request(RequestKind.ADD),
            time=Time(TimeKind.RELATIVE, relation_id=relation.id),
            relations=(relation,),
        )
        self.assertEqual(event.time.relation_id, relation.id)

        with self.assertRaisesRegex(ValueError, "relation"):
            Event(
                id="event-broken",
                item_ids=("item-report",),
                content=(content,),
                kind=Kind.TASK,
                request=Request(RequestKind.ADD),
                time=Time(TimeKind.RELATIVE, relation_id="missing"),
            )

    def test_residue_must_anchor_an_event_source_item(self) -> None:
        content = Content("item-a", Span(0, 1), "A")
        with self.assertRaisesRegex(ValueError, "source item"):
            Event(
                id="event-a",
                item_ids=("item-a",),
                content=(content,),
                kind=Kind.TASK,
                request=Request(RequestKind.ADD),
                time=Time(TimeKind.NONE),
                residue=(
                    Residue(
                        "item-b",
                        Span(2, 4),
                        "状态好",
                        ResidueReason.UNSUPPORTED,
                        "test-v1",
                    ),
                ),
            )

    def test_gap_carries_semantic_candidates_without_becoming_field_completion(self) -> None:
        gap = Gap(
            item_id="item-1",
            event_id="event-1",
            field="relations[0].target",
            question="你指的是哪一个 Research？",
            reason=GapReason.AMBIGUOUS,
            candidates=("research-morning", "research-evening"),
        )

        self.assertEqual(gap.candidates, ("research-morning", "research-evening"))
        with self.assertRaisesRegex(ValueError, "unique"):
            Gap(
                "item-1",
                "time",
                "哪一个？",
                GapReason.AMBIGUOUS,
                candidates=("same", "same"),
            )

    def test_residue_records_interpreter_version_and_handling_status(self) -> None:
        residue = Residue(
            item_id="item-1",
            span=Span(0, 5),
            text="脑子清醒时",
            reason=ResidueReason.UNSUPPORTED,
            interpreter_version="events-v1",
            hint="state-dependent timing",
        )

        self.assertEqual(residue.status, ResidueStatus.OPEN)
        self.assertEqual(residue.interpreter_version, "events-v1")

    def test_snapshot_is_complete_and_represents_every_item(self) -> None:
        item = Item("item-1", "prompt-1", Span(0, 2), "日语")
        event = Event(
            id="event-1",
            item_ids=(item.id,),
            content=(Content(item.id, item.span, item.text),),
            kind=Kind.TASK,
            request=Request(RequestKind.ADD),
            time=Time(TimeKind.PERIOD, period=Period.AFTERNOON),
            duration=Duration(DurationKind.EXACT, minutes=30),
        )

        snapshot = Snapshot("snapshot-1", "prompt-1", 1, (item,), (event,))

        self.assertEqual(snapshot.events, (event,))
        with self.assertRaisesRegex(ValueError, "every item"):
            Snapshot("snapshot-2", "prompt-1", 1, (item,), ())
