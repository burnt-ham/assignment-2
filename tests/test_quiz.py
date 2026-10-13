"""Unit tests: quiz validation, the fixed answer key, and scoring."""

import dataclasses

import pytest

from course_assistant.quiz import QUIZ_SYSTEM_PROMPT, QuizError, QuizModel, grade, offline_questions, validate_questions
from course_assistant.retrieval import Evidence
from course_assistant.services import ServiceError
from conftest import FakeChat

SOURCE = "Chunking divides parsed content into useful passages while preserving source metadata such as page numbers."


def evidence():
    return [Evidence("E1", "text", "d1", "notes.pdf", 2, "page", "Chunking", SOURCE, "", 1.0)]


def question(**changes):
    base = {
        "question": "What does chunking preserve?",
        "options": ["Source metadata", "Model weights", "API keys", "Nothing"],
        "correct_option": "A",
        "explanation": "Chunks keep source metadata.",
        "evidence_id": "E1",
        "quote": "preserving source metadata",
        "concept": "Chunking",
        "learning_target": "Explain what chunking preserves",
    }
    base.update(changes)
    return base


def test_valid_questions_pass():
    good, problems = validate_questions(QuizModel(questions=[question()]), evidence())
    assert len(good) == 1 and not problems
    assert good[0].correct_letter == "A"


def test_quiz_prompt_requests_source_scoped_concepts_and_learning_targets():
    assert '"concept"' in QUIZ_SYSTEM_PROMPT
    assert '"learning_target"' in QUIZ_SYSTEM_PROMPT
    assert "same-domain" in QUIZ_SYSTEM_PROMPT


def test_questions_keep_concept_and_learning_target():
    item = question(
        concept="Chunking",
        learning_target="Explain what source metadata a chunk preserves",
    )
    good, problems = validate_questions(QuizModel(questions=[item]), evidence())
    assert not problems
    assert (good[0].concept, good[0].learning_target) == (
        "Chunking", "Explain what source metadata a chunk preserves"
    )


def test_question_without_concept_or_learning_target_is_rejected():
    good, problems = validate_questions(
        QuizModel(questions=[question(concept="", learning_target="")]), evidence()
    )
    assert not good
    assert "concept" in problems[0].lower() or "learning target" in problems[0].lower()


def test_case_and_whitespace_duplicate_answer_choices_are_rejected():
    good, problems = validate_questions(
        QuizModel(questions=[question(options=["Source metadata", " source METADATA ", "Model weights", "API keys"])]),
        evidence(),
    )
    assert not good
    assert "repeated" in problems[0]


def test_generic_all_or_none_distractors_are_rejected():
    good, problems = validate_questions(
        QuizModel(questions=[question(options=["Source metadata", "Model weights", "API keys", "All of the above"])]),
        evidence(),
    )
    assert not good
    assert "generic" in problems[0]


@pytest.mark.parametrize("concept", ["Week 2", "Week 2: Introduction", "Module 4 - Review"])
def test_week_number_is_not_a_concept_category(concept):
    good, problems = validate_questions(
        QuizModel(questions=[question(concept=concept)]), evidence()
    )
    assert not good
    assert "concept" in problems[0].lower()


def test_model_option_letters_remain_in_answer_text():
    labeled = ["A. Source metadata", "B. Model weights", "C. API keys", "D. Nothing"]
    good, problems = validate_questions(QuizModel(questions=[question(options=labeled)]), evidence())
    assert not problems
    assert good[0].options == tuple(labeled)


def test_ordered_scientific_initials_are_preserved():
    source = Evidence("E1", "text", "d1", "species.md", 1, "section", "Species", "A. fumigatus is discussed here.", "", 1.0)
    options = ["A. fumigatus", "B. subtilis", "C. difficile", "D. radiodurans"]
    item = question(
        question="Which organism appears in the source?", options=options,
        quote="A. fumigatus", concept="Species", learning_target="Identify the named organism",
    )
    good, problems = validate_questions(QuizModel(questions=[item]), [source])
    assert not problems
    assert good[0].options == tuple(options)


def test_legitimate_abbreviated_answer_is_not_stripped():
    source = Evidence("E1", "text", "d1", "microbiology.md", 1, "page", "Pathogens", "C. difficile causes infection.", "", 1.0)
    item = question(
        question="Which organism does the source discuss?",
        options=["C. difficile", "Norovirus", "Influenza", "E. coli"],
        correct_option="A", quote="C. difficile", concept="Pathogens",
        learning_target="Identify the organism named in the source",
    )
    good, problems = validate_questions(QuizModel(questions=[item]), [source])
    assert not problems
    assert good[0].options[0] == "C. difficile"
    assert good[0].correct_index == 0


def test_distinct_programming_language_symbols_are_not_conflated():
    source = Evidence("E1", "text", "d1", "languages.md", 1, "section", "Languages", "C++ and C# are distinct programming languages.", "", 1.0)
    item = question(
        question="Which language is named first in the source?",
        options=["C++", "C#", "Python", "Java"],
        quote="C++", concept="Programming languages",
        learning_target="Distinguish C++ from C#",
    )
    good, problems = validate_questions(QuizModel(questions=[item]), [source])
    assert not problems
    assert good[0].options == ("C++", "C#", "Python", "Java")


def test_distinct_programming_syntax_is_not_conflated():
    source = Evidence("E1", "text", "d1", "syntax.md", 1, "section", "Syntax", "foo.bar accesses an attribute.", "", 1.0)
    item = question(
        question="Which syntax accesses an attribute?",
        options=["foo.bar", "foo bar", "foo#bar", "foobar"],
        quote="foo.bar", concept="Attribute access",
        learning_target="Identify attribute access syntax",
    )
    good, problems = validate_questions(QuizModel(questions=[item]), [source])
    assert not problems
    assert good[0].options[0] == "foo.bar"


def test_one_letter_answer_is_not_treated_as_blank_article():
    source = Evidence("E1", "text", "d1", "blood.md", 1, "section", "Blood types", "Blood type A has A antigens.", "", 1.0)
    item = question(
        question="Which blood type is described?",
        options=["A", "B", "AB", "O"], quote="Blood type A",
        concept="Blood types", learning_target="Identify blood type A",
    )
    good, problems = validate_questions(QuizModel(questions=[item]), [source])
    assert not problems
    assert good[0].options[0] == "A"


@pytest.mark.parametrize(
    "changes",
    [
        {"options": ["Same", "same", "Other", "Another"]},
        {"correct_option": "E"},
        {"evidence_id": "E7"},
        {"quote": "chunking deletes page numbers"},
    ],
)
def test_bad_questions_are_dropped(changes):
    good, problems = validate_questions(QuizModel(questions=[question(**changes)]), evidence())
    assert good == [] and len(problems) == 1


def test_replaces_rejected_questions_up_to_requested_count(make_assistant, sample_pdf):
    first = question(
        question="What does RAG retrieve?",
        options=["Relevant information", "Model weights", "API keys", "Nothing"],
        quote="retrieves relevant information",
        concept="RAG", learning_target="Identify what RAG retrieves",
    )
    invalid = question(options=["Duplicate", " duplicate ", "Other", "Another"])
    replacement = question(
        question="What does RAG add to the model context?",
        options=["Retrieved information", "Passwords", "Model weights", "Nothing"],
        quote="adds it to the model context",
        concept="RAG", learning_target="Explain how RAG augments model context",
    )
    chat = FakeChat([{"questions": [first, invalid]}, {"questions": [replacement]}])
    assistant = make_assistant(chat=chat)
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert len(quiz.questions) == 2
    assert len(chat.calls) == 2
    assert quiz.questions[1].question == replacement["question"]
    assert any("dropped" in note for note in quiz.notes)


def test_malformed_question_does_not_discard_valid_batchmate(make_assistant, sample_pdf):
    first = question(
        question="What does RAG retrieve?", options=["Relevant information", "Weights", "Keys", "Nothing"],
        quote="retrieves relevant information", concept="RAG", learning_target="Identify retrieved information",
    )
    replacement = question(
        question="What does RAG add to the model context?",
        options=["Retrieved information", "Weights", "Keys", "Nothing"],
        quote="adds it to the model context", concept="RAG", learning_target="Explain context augmentation",
    )
    chat = FakeChat([{"questions": [first, {"question": "Missing options"}]}, {"questions": [replacement]}])
    assistant = make_assistant(chat=chat)
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert [q.question for q in quiz.questions] == [first["question"], replacement["question"]]
    assert len(chat.calls) == 2
    assert any("dropped" in note for note in quiz.notes)


def test_reworded_same_answer_target_gets_replaced(make_assistant, sample_pdf):
    first = question(
        question="What does RAG retrieve?", options=["Relevant information", "Weights", "Keys", "Nothing"],
        quote="retrieves relevant information", concept="RAG", learning_target="Identify what RAG retrieves",
    )
    reworded = question(
        question="Which information does RAG retrieve?", options=["Relevant information", "Weights", "Keys", "Nothing"],
        quote="retrieves relevant information", concept="RAG", learning_target="Identify what RAG retrieves",
    )
    different = question(
        question="What does RAG add to the model context?", options=["Retrieved information", "Weights", "Keys", "Nothing"],
        quote="adds it to the model context", concept="RAG", learning_target="Explain how RAG augments context",
    )
    chat = FakeChat([{"questions": [first]}, {"questions": [reworded]}, {"questions": [different]}])
    assistant = make_assistant(chat=chat)
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert [q.question for q in quiz.questions] == [first["question"], different["question"]]
    assert len(chat.calls) == 3


def test_keeps_valid_questions_if_replacement_service_fails(make_assistant, sample_pdf):
    first = question(
        question="What does RAG retrieve?", options=["Relevant information", "Weights", "Keys", "Nothing"],
        quote="retrieves relevant information", concept="RAG", learning_target="Identify what RAG retrieves",
    )

    class FailAfterFirst(FakeChat):
        def complete(self, *args, **kwargs):
            if self.calls:
                raise ServiceError("replacement request failed")
            return super().complete(*args, **kwargs)

    chat = FailAfterFirst([{"questions": [first]}])
    assistant = make_assistant(chat=chat)
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert len(quiz.questions) == 1
    assert quiz.questions[0].question == first["question"]
    assert quiz.source == chat.name
    assert any("replacement" in note.lower() for note in quiz.notes)


def test_answer_key_is_fixed_and_scores_match(make_assistant, sample_pdf):
    quote = "retrieves relevant information"  # from page 1, which is E1 when no topic is given
    replies = [{"questions": [
        question(question="What does RAG retrieve?", options=["Relevant information", "Model weights", "Passwords", "Nothing"], quote=quote),
        question(question="What does RAG add to the model context?", options=["Retrieved information", "Random text", "Images only", "Nothing"], correct_option="A", quote=quote),
    ]}]
    assistant = make_assistant(chat=FakeChat(replies))
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert len(quiz.questions) == 2
    key, fingerprint = quiz.answer_key, quiz.key_fingerprint
    with pytest.raises(dataclasses.FrozenInstanceError):
        quiz.questions[0].correct_index = 3
    result = grade(quiz, {0: quiz.questions[0].correct_index, 1: (quiz.questions[1].correct_index + 1) % 4})
    assert (result.correct, result.answered, result.total) == (1, 2, len(quiz.questions))
    assert quiz.answer_key == key and quiz.key_fingerprint == fingerprint


def test_unanswered_questions_are_not_scored(make_assistant, sample_pdf):
    assistant = make_assistant()
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    result = grade(quiz, {})
    assert result.correct == 0 and result.answered == 0


def test_offline_questions_are_answerable_from_their_source():
    items = evidence() + [Evidence("E2", "text", "d1", "notes.pdf", 3, "page", "Reranking", "Reranking compares candidate chunks to the original question and prioritizes them.", "", 1.0)]
    questions = offline_questions(items, 2)
    assert questions
    for q in questions:
        answer = q.options[q.correct_index]
        assert answer.lower() in q.evidence.text.lower()
        assert "_____" in q.question and answer.lower() not in q.question.lower()
        assert q.concept == q.evidence.title
        assert q.learning_target == f"Recall the source term: {answer}"


def test_unfocused_quiz_draws_evidence_from_each_document(make_assistant, sample_pdf, sample_md):
    assistant = make_assistant()
    pdf = assistant.library.add_file(sample_pdf).document
    md = assistant.library.add_file(sample_md).document
    evidence_items = assistant.quiz_maker.gather_evidence([pdf.doc_id, md.doc_id], "", limit=2)
    assert {item.doc_id for item in evidence_items} == {pdf.doc_id, md.doc_id}


def test_unfocused_quiz_spreads_within_a_long_document(make_assistant, sample_pdf):
    assistant = make_assistant()
    pdf = assistant.library.add_file(sample_pdf).document
    evidence_items = assistant.quiz_maker.gather_evidence([pdf.doc_id], "", limit=2)
    assert {item.page for item in evidence_items} == {1, 3}


def test_unfocused_quiz_ignores_nonpositive_evidence_limit(make_assistant, sample_pdf):
    assistant = make_assistant()
    pdf = assistant.library.add_file(sample_pdf).document
    assert assistant.quiz_maker.gather_evidence([pdf.doc_id], "", limit=-1) == []


def test_quiz_needs_a_document(make_assistant):
    with pytest.raises(QuizError, match="Choose at least one document"):
        make_assistant().quiz_maker.make_quiz([], "", 3)


def test_quiz_service_error_details_are_not_exposed(make_assistant, sample_pdf):
    class EchoingFailure(FakeChat):
        def complete(self, *args, **kwargs):
            raise ServiceError("request failed with TOP_SECRET_VALUE")

    assistant = make_assistant(chat=EchoingFailure())
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=1)
    assert quiz.questions
    assert "TOP_SECRET_VALUE" not in " ".join(quiz.notes)
    assert any("unavailable" in note for note in quiz.notes)


def test_quiz_service_down_uses_offline_questions(make_assistant, sample_pdf):
    assistant = make_assistant(chat=FakeChat(fail=True))
    doc = assistant.library.add_file(sample_pdf).document
    quiz = assistant.quiz_maker.make_quiz([doc.doc_id], count=2)
    assert quiz.source == "offline generator"
    assert any("unavailable" in n for n in quiz.notes)
