"""Overview supervisor and reader names for report exports."""

from __future__ import annotations

from thesis2lib.individual_assessment import final_attempt_individual_data
from thesis2lib.models import ProcessedStudent


def overview_names_not_applicable(item: ProcessedStudent) -> bool:
    return item.final_attempt is None


def overview_supervisor_name(item: ProcessedStudent) -> str:
    if overview_names_not_applicable(item):
        return "Not applicable"
    archive = item.archive_metadata
    if archive and archive.supervisor_name:
        return archive.supervisor_name
    return ""


def overview_first_reader_name(item: ProcessedStudent) -> str:
    if overview_names_not_applicable(item):
        return "Not applicable"
    final = final_attempt_individual_data(
        item.final_attempt, item.individual_assessments
    )
    if final and final.first_reader and final.first_reader.reader_name:
        return final.first_reader.reader_name
    archive = item.archive_metadata
    if archive and archive.supervisor_name:
        return archive.supervisor_name
    return ""


def overview_second_reader_name(item: ProcessedStudent) -> str:
    if overview_names_not_applicable(item):
        return "Not applicable"
    final = final_attempt_individual_data(
        item.final_attempt, item.individual_assessments
    )
    if final and final.second_reader and final.second_reader.reader_name:
        return final.second_reader.reader_name
    archive = item.archive_metadata
    if archive and archive.second_reader_name:
        return archive.second_reader_name
    return ""
