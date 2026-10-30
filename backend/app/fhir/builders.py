"""FHIR R4 資源產生器（TW Core 精簡版）。Adapter、Mock 與示範資料共用，確保形狀一致。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.fhir import codes as c

Resource = dict[str, Any]


def _meta(resource_type: str) -> dict[str, Any]:
    return {"profile": [c.PROFILE[resource_type]]}


def _concept(system: str, code: str, display: str | None = None) -> dict[str, Any]:
    coding: dict[str, Any] = {"system": system, "code": code}
    if display:
        coding["display"] = display
    return {"coding": [coding], **({"text": display} if display else {})}


def _quantity(value: float, unit: str, ucum: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": ucum}


def observation(
    code: str,
    display: str,
    effective: datetime,
    *,
    value: dict[str, Any] | None = None,
    components: list[dict[str, Any]] | None = None,
    period_end: datetime | None = None,
    performer: str | None = None,
    device: str | None = None,
    category: str = "vital-signs",
) -> Resource:
    res: Resource = {
        "resourceType": "Observation",
        "meta": _meta("Observation"),
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": category,
                    }
                ]
            }
        ],
        "code": _concept(c.LOINC, code, display),
    }
    if period_end is not None:
        res["effectivePeriod"] = {"start": effective.isoformat(), "end": period_end.isoformat()}
    else:
        res["effectiveDateTime"] = effective.isoformat()
    if value is not None:
        res["valueQuantity"] = value
    if components is not None:
        res["component"] = components
    if performer is not None:
        res["performer"] = [{"identifier": {"system": "urn:healthapp:site", "value": performer}}]
    if device is not None:
        res["device"] = {"identifier": {"value": device}}
    return res


def steps_daily(day_end: datetime, steps: int) -> Resource:
    return observation(
        c.STEPS_DAILY,
        "Number of steps in 24 hour Measured",
        day_end,
        value=_quantity(steps, "steps", "{steps}"),
        category="activity",
    )


def steps_hourly(hour_start: datetime, steps: int) -> Resource:
    return observation(
        c.STEPS_HOURLY,
        "Number of steps Pedometer",
        hour_start,
        value=_quantity(steps, "steps", "{steps}"),
        period_end=hour_start + timedelta(hours=1),
        category="activity",
    )


def heart_rate(hour_start: datetime, bpm: int) -> Resource:
    return observation(
        c.HEART_RATE,
        "Heart rate",
        hour_start,
        value=_quantity(bpm, "beats/min", "/min"),
        period_end=hour_start + timedelta(hours=1),
    )


def blood_pressure(
    at: datetime,
    systolic: int,
    diastolic: int,
    *,
    site: str | None = None,
    device: str | None = None,
) -> Resource:
    return observation(
        c.BP_PANEL,
        "Blood pressure panel",
        at,
        performer=site,
        device=device,
        components=[
            {
                "code": _concept(c.LOINC, c.BP_SYSTOLIC, "Systolic blood pressure"),
                "valueQuantity": _quantity(systolic, "mmHg", "mm[Hg]"),
            },
            {
                "code": _concept(c.LOINC, c.BP_DIASTOLIC, "Diastolic blood pressure"),
                "valueQuantity": _quantity(diastolic, "mmHg", "mm[Hg]"),
            },
        ],
    )


def body_weight(at: datetime, kg: float, *, site: str | None = None) -> Resource:
    return observation(
        c.BODY_WEIGHT, "Body weight", at, value=_quantity(kg, "kg", "kg"), performer=site
    )


def body_height(at: datetime, cm: float, *, site: str | None = None) -> Resource:
    return observation(
        c.BODY_HEIGHT, "Body height", at, value=_quantity(cm, "cm", "cm"), performer=site
    )


def body_fat(at: datetime, percent: float, *, site: str | None = None) -> Resource:
    return observation(
        c.BODY_FAT, "Body fat percentage", at, value=_quantity(percent, "%", "%"), performer=site
    )


def glucose(at: datetime, mg_dl: float, *, site: str | None = None) -> Resource:
    return observation(
        c.GLUCOSE,
        "Glucose [Mass/volume] in Blood",
        at,
        value=_quantity(mg_dl, "mg/dL", "mg/dL"),
        performer=site,
    )


def lab(at: datetime, code: str, display: str, value: float, unit: str) -> Resource:
    return observation(code, display, at, value=_quantity(value, unit, unit), category="laboratory")


def encounter(start: datetime, department: str, *, hospital: str = "示範醫院") -> Resource:
    return {
        "resourceType": "Encounter",
        "meta": _meta("Encounter"),
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": c.ENCOUNTER_AMB,
            "display": "ambulatory",
        },
        "serviceType": {"text": department},
        "serviceProvider": {"display": hospital},
        "period": {"start": start.isoformat()},
    }


def condition(recorded: date, icd10: str, display: str) -> Resource:
    return {
        "resourceType": "Condition",
        "meta": _meta("Condition"),
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                }
            ]
        },
        "code": _concept(c.ICD10, icd10, display),
        "recordedDate": recorded.isoformat(),
    }


def medication_statement(start: date, atc: str, display: str) -> Resource:
    return {
        "resourceType": "MedicationStatement",
        "meta": _meta("MedicationStatement"),
        "status": "active",
        "medicationCodeableConcept": _concept(c.ATC, atc, display),
        "effectivePeriod": {"start": start.isoformat()},
    }


def immunization(at: date, cvx: str, display: str) -> Resource:
    return {
        "resourceType": "Immunization",
        "meta": _meta("Immunization"),
        "status": "completed",
        "vaccineCode": _concept(c.CVX, cvx, display),
        "occurrenceDateTime": at.isoformat(),
    }


def allergy(recorded: date, code: str, display: str) -> Resource:
    return {
        "resourceType": "AllergyIntolerance",
        "meta": _meta("AllergyIntolerance"),
        "code": {"text": display, "coding": [{"code": code, "display": display}]},
        "recordedDate": recorded.isoformat(),
    }
