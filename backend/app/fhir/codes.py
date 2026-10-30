"""FHIR 編碼常數。Observation 用 LOINC，診斷用 ICD-10-CM（健保申報碼）。"""

from __future__ import annotations

LOINC = "http://loinc.org"
ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"
ATC = "http://www.whocc.no/atc"
CVX = "http://hl7.org/fhir/sid/cvx"

TWCORE = "https://twcore.mohw.gov.tw/ig/twcore/StructureDefinition"
PROFILE = {
    "Observation": f"{TWCORE}/Observation-twcore",
    "Condition": f"{TWCORE}/Condition-twcore",
    "Encounter": f"{TWCORE}/Encounter-twcore",
    "MedicationStatement": f"{TWCORE}/MedicationStatement-twcore",
    "Immunization": f"{TWCORE}/Immunization-twcore",
    "AllergyIntolerance": f"{TWCORE}/AllergyIntolerance-twcore",
}

STEPS_DAILY = "41950-7"  # Number of steps in 24 hour Measured
STEPS_HOURLY = "55423-8"  # Number of steps in unspecified time Pedometer
HEART_RATE = "8867-4"
BP_PANEL = "85354-9"
BP_SYSTOLIC = "8480-6"
BP_DIASTOLIC = "8462-4"
BODY_WEIGHT = "29463-7"
BODY_HEIGHT = "8302-2"
BODY_FAT = "41982-0"
GLUCOSE = "2339-0"
HBA1C = "4548-4"
ENCOUNTER_AMB = "AMB"

# 慢性病判斷（運動處方保守版、群體統計）：ICD-10 前綴
HYPERTENSION = ("I10", "I11", "I12", "I13", "I15")
DIABETES = ("E10", "E11", "E13", "E14")
KIDNEY = ("N18", "N19")
CARDIOVASCULAR = ("I20", "I21", "I22", "I24", "I25", "I48", "I50", "I63")

# 每個資料項目由哪個授權項目管轄（consent scope 的 read 清單）
SITE_ITEM_OF_CODE = {
    BP_PANEL: "bp",
    BODY_WEIGHT: "weight",
    BODY_HEIGHT: "height",
    BODY_FAT: "body_fat",
    GLUCOSE: "glucose",
}
WEARABLE_ITEM_OF_CODE = {STEPS_DAILY: "steps", STEPS_HOURLY: "steps", HEART_RATE: "heart_rate"}
NHI_ITEM_OF_TYPE = {
    "Encounter": "visits",
    "Condition": "conditions",
    "MedicationStatement": "medications",
    "Observation": "labs",
    "Immunization": "immunizations",
    "AllergyIntolerance": "allergies",
}
