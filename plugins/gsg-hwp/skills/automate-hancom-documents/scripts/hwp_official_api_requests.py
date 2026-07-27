from __future__ import annotations

import base64
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal, cast

from hwp_official_api_contract import (
    OfficialAction,
    OfficialApiCatalog,
    OfficialAutomationMember,
)
from hwp_official_api_runtime import OfficialApiRuntimeCase, build_runtime_cases


@dataclass(frozen=True, slots=True)
class RuntimeFixturePaths:
    input_hwp: Path
    output_hwp: Path
    input_image: Path
    output_image: Path

    @classmethod
    def defaults(cls) -> RuntimeFixturePaths:
        root = Path(tempfile.gettempdir()) / "HancomApiHarness"
        return cls(
            input_hwp=root / "probe-input.hwp",
            output_hwp=root / "probe-output.hwp",
            input_image=root / "probe-input.bmp",
            output_image=root / "probe-output.png",
        )


type OfficialApiEffect = Literal[
    "none",
    "document_save",
    "document_close",
    "application_quit",
    "document_discard",
]
type OfficialApiTargetScope = Literal[
    "none",
    "document",
    "application",
    "window",
    "tab",
    "artifact",
    "inner_list",
    "unknown",
]
type OfficialApiSafetyClassification = Literal[
    "catalog_metadata",
    "inherited_catalog_metadata",
    "unclassified",
]
type OfficialApiArgumentSafetyIssue = Literal[
    "destructive_bool_fail_safe_unverified",
    "raw_arguments_differ_from_catalog_fail_safe",
]

# ARG-level generator cause; the broader outward policy error remains
# DESTRUCTIVE_ARGUMENT_UNVERIFIED.
_DESTRUCTIVE_BOOL_FAIL_SAFE_UNVERIFIED: Final = (
    "DESTRUCTIVE_BOOL_FAIL_SAFE_UNVERIFIED"
)


@dataclass(frozen=True, slots=True)
class OfficialApiSafety:
    effect: OfficialApiEffect
    target_scope: OfficialApiTargetScope
    classification: OfficialApiSafetyClassification
    rationale: str
    dialog_policy_control: bool = False
    indirect_execution: bool = False
    action_routing_control: bool = False
    arguments_fail_safe: bool = True
    argument_safety_issue: OfficialApiArgumentSafetyIssue | None = None
    fixture_scope: Literal[
        "active_document_owner_context",
        "server_owned_process_document",
    ] = "active_document_owner_context"
    fixture_isolation_guaranteed: bool = False

    @property
    def destructive(self) -> bool:
        return self.effect != "none"


_SAFE_CATALOG_OPERATION = OfficialApiSafety(
    effect="none",
    target_scope="none",
    classification="catalog_metadata",
    rationale="catalog metadata does not describe a document lifecycle effect",
)
_UNCLASSIFIED_OPERATION = OfficialApiSafety(
    effect="none",
    target_scope="unknown",
    classification="unclassified",
    rationale="request has no catalog-derived safety classification",
)


@dataclass(frozen=True, slots=True)
class OfficialApiNativeRequest:
    case_id: str
    category: str
    name: str
    owner: str | None
    member_kind: str | None
    source_page: int
    payload: str
    input_lines: tuple[str, ...]
    safety: OfficialApiSafety = _UNCLASSIFIED_OPERATION


_MANUAL_DECLARATIONS = {
    "RegisterModule": "BOOL RegisterModule(BSTR ModuleType, BSTR ModuleData)",
    "InitHParameterSet": "void InitHParameterSet(void)",
    "GetPosBySet": "LPDISPATCH GetPosBySet(void)",
    "UnSelectCtrl": "void UnSelectCtrl(void)",
    "GetMessageBoxMode": "long GetMessageBoxMode(void)",
    "GetHeadingString": "BSTR GetHeadingString(void)",
    "ProtectPrivateInfo": (
        "bool ProtectPrivateInfo(BSTR ProtectingChar, VARIANT PrivatePatternType)"
    ),
    "ScanFont": "void ScanFont(void)",
    "LBText": "BSTR LBText(long index)",
}

# This is a legacy HCV1 wire token understood by protocol-6 native bridges. It
# prepares an owner-specific object in the active document; it is not evidence
# of a separate process, document, or tab.
_LEGACY_OWNER_FIXTURE_DIRECTIVE = "FIXTURE\tISOLATED"
_NAME_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+"
)


def _name_tokens(name: str) -> tuple[str, ...]:
    matches = cast(list[str], _NAME_TOKEN.findall(name))
    return tuple(token.lower() for token in matches)


def classify_official_action_safety(action: OfficialAction) -> OfficialApiSafety:
    tokens = _name_tokens(action.name)
    lifecycle_tokens = tokens[2:] if tokens[:2] == ("hwp", "ctrl") else tokens
    description = " ".join((action.description, action.remarks)).casefold()
    normalized_description = description.replace(" ", "")
    macro_execution = (
        "macro" in tokens
        and bool({"play", "repeat", "run", "execute"}.intersection(tokens))
    ) or (
        ("매크로" in normalized_description or "macro" in normalized_description)
        and any(
            marker in normalized_description
            for marker in ("실행", "execute", "run", "play")
        )
        and not any(
            marker in normalized_description
            for marker in (
                "중지",
                "일시",
                "정의",
                "보안",
                "stop",
                "pause",
                "define",
                "security",
            )
        )
    )
    if macro_execution:
        return OfficialApiSafety(
            effect="none",
            target_scope="application",
            classification="catalog_metadata",
            rationale="catalog action executes document-supplied macro code",
            indirect_execution=True,
        )

    if "문서" in description and "저장" in description:
        return OfficialApiSafety(
            effect="document_save",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog description says that the current document is saved",
        )
    if "문서" in description and "닫" in description:
        return OfficialApiSafety(
            effect="document_close",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog description says that the current document is closed",
        )
    if lifecycle_tokens[:2] == ("file", "close"):
        return OfficialApiSafety(
            effect="document_close",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog action targets a file document close operation",
        )
    if lifecycle_tokens[:2] == ("file", "quit"):
        return OfficialApiSafety(
            effect="application_quit",
            target_scope="application",
            classification="catalog_metadata",
            rationale="catalog action targets application termination",
        )
    if lifecycle_tokens[:2] == ("file", "save"):
        suffix = lifecycle_tokens[2:]
        artifact_or_dialog = frozenset({"image", "picture", "option", "dialog", "dlg"})
        if not artifact_or_dialog.intersection(suffix):
            return OfficialApiSafety(
                effect="document_save",
                target_scope="document",
                classification="catalog_metadata",
                rationale="catalog action targets the current document file",
            )
        return OfficialApiSafety(
            effect="none",
            target_scope="artifact",
            classification="catalog_metadata",
            rationale="catalog action saves an artifact or configures a dialog",
        )
    if lifecycle_tokens[:2] == ("version", "save") or (
        lifecycle_tokens[:1] == ("save",)
        and "버전" in description
        and "저장" in description
    ):
        return OfficialApiSafety(
            effect="document_save",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog metadata describes saving a document version",
        )
    if lifecycle_tokens[:1] == ("close",) and "리스트" in description:
        return OfficialApiSafety(
            effect="none",
            target_scope="inner_list",
            classification="catalog_metadata",
            rationale="catalog action closes an inner list, not the document",
        )
    if "save" in lifecycle_tokens:
        return OfficialApiSafety(
            effect="none",
            target_scope="artifact",
            classification="catalog_metadata",
            rationale="catalog save action does not target the live document file",
        )
    return _SAFE_CATALOG_OPERATION


def _automation_target_scope(
    member: OfficialAutomationMember,
) -> OfficialApiTargetScope:
    owner_tokens = _name_tokens(member.owner)
    description = member.description.casefold()
    if (
        "document" in owner_tokens
        or "documents" in owner_tokens
        or "문서" in description
    ):
        return "document"
    if "window" in owner_tokens or "windows" in owner_tokens or "윈도우" in description:
        return "window"
    if "tab" in owner_tokens or "tabs" in owner_tokens or "탭" in description:
        return "tab"
    if "hwp" in owner_tokens and "object" in owner_tokens:
        return "application"
    return "unknown"


def classify_official_automation_safety(
    member: OfficialAutomationMember,
) -> OfficialApiSafety:
    if member.member_kind != "method":
        return _SAFE_CATALOG_OPERATION
    operation = _name_tokens(member.name)
    target_scope = _automation_target_scope(member)
    details_description = re.split(
        r"\nDeclaration\b",
        member.details,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    description = " ".join((member.description, details_description)).casefold()
    normalized_description = re.sub(r"\s+", "", description)
    query_operation = operation[:1] in {
        ("get",),
        ("is",),
        ("can",),
        ("has",),
        ("check",),
        ("query",),
        ("find",),
        ("read",),
    }
    query_is_observational = (
        query_operation
        and any(
            marker in normalized_description
            for marker in (
                "수있는지",
                "가능한지",
                "여부",
                "경로",
                "상태",
                "확인",
                "조사",
                "얻",
                "조회",
                "존재",
            )
        )
        and not any(
            marker in normalized_description
            for marker in (
                "닫고",
                "저장하고",
                "종료하고",
                "버리고",
                "폐기하고",
                "지우고",
            )
        )
    )
    if query_is_observational:
        return _SAFE_CATALOG_OPERATION

    action_routing_control = (
        "action" in normalized_description or "액션" in normalized_description
    ) and any(
        marker in normalized_description
        for marker in (
            "replace",
            "redirect",
            "remap",
            "대체",
            "교체",
            "바꾸",
            "매핑",
        )
    )
    if action_routing_control:
        return OfficialApiSafety(
            effect="none",
            target_scope="application",
            classification="catalog_metadata",
            rationale="catalog method changes the meaning of later action identifiers",
            action_routing_control=True,
        )

    if (
        (
            "메시지박스" in normalized_description
            or "messagebox" in normalized_description
        )
        and "자동" in normalized_description
        and "클릭" in normalized_description
    ):
        return OfficialApiSafety(
            effect="none",
            target_scope="application",
            classification="catalog_metadata",
            rationale=(
                "catalog method changes persistent automatic dialog-confirmation policy"
            ),
            dialog_policy_control=True,
        )
    if ("매크로" in normalized_description and "실행" in normalized_description) or (
        ("macro" in normalized_description or "script" in normalized_description)
        and any(
            marker in normalized_description for marker in ("execute", "run", "invoke")
        )
    ):
        return OfficialApiSafety(
            effect="none",
            target_scope="application",
            classification="catalog_metadata",
            rationale="catalog method executes document-supplied macro code",
            indirect_execution=True,
        )

    if (
        "문서" in description
        and "내용" in description
        and "닫" in description
        and "빈문서" in description
    ):
        return OfficialApiSafety(
            effect="document_discard",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog description discards document contents for a blank document",
        )
    if "문서" in description and "저장" in description:
        return OfficialApiSafety(
            effect="document_save",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog description says that the current document is saved",
        )
    if "문서" in description and "닫" in description:
        return OfficialApiSafety(
            effect="document_close",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog description says that the current document is closed",
        )
    if "문서" in description and "종료" in description:
        return OfficialApiSafety(
            effect="document_close",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog description says that the current document terminates",
        )
    if "종료" in description and any(
        subject in description for subject in ("한/글", "프로그램", "애플리케이션")
    ):
        return OfficialApiSafety(
            effect="application_quit",
            target_scope="application",
            classification="catalog_metadata",
            rationale="catalog description says that the application terminates",
        )
    if operation in {("save",), ("save", "as")} and (
        target_scope == "document" or "문서" in description
    ):
        return OfficialApiSafety(
            effect="document_save",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog method saves the current document",
        )
    if operation == ("clear",) and (
        target_scope == "document" or "문서" in description
    ):
        return OfficialApiSafety(
            effect="document_discard",
            target_scope="document",
            classification="catalog_metadata",
            rationale="catalog method discards the current document contents",
        )
    if operation == ("close",) and target_scope in {
        "document",
        "window",
        "tab",
    }:
        return OfficialApiSafety(
            effect="document_close",
            target_scope=target_scope,
            classification="catalog_metadata",
            rationale=f"catalog method closes the active {target_scope}",
        )
    if operation[:1] in {("terminate",), ("exit",)}:
        if target_scope == "document":
            return OfficialApiSafety(
                effect="document_close",
                target_scope="document",
                classification="catalog_metadata",
                rationale="catalog method terminates the current document",
            )
        return OfficialApiSafety(
            effect="application_quit",
            target_scope="application",
            classification="catalog_metadata",
            rationale="catalog method terminates the application",
        )
    if operation == ("quit",):
        return OfficialApiSafety(
            effect="application_quit",
            target_scope="application",
            classification="catalog_metadata",
            rationale="catalog method terminates the application",
        )
    return _SAFE_CATALOG_OPERATION


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _action_parameter_map(catalog: OfficialApiCatalog) -> dict[str, tuple[str, ...]]:
    result: defaultdict[str, list[str]] = defaultdict(list)
    for action in catalog.actions:
        token = action.parameter_set
        if token is None:
            continue
        normalized = token.rstrip("*")
        if normalized not in {"", "+"}:
            result[normalized].append(action.name)
    return {name: tuple(actions) for name, actions in result.items()}


def _parameter_text(declaration: str) -> tuple[str, ...]:
    start = declaration.find("(")
    end = declaration.find(")", start + 1)
    if start < 0 or end < start:
        return ()
    body = declaration[start + 1 : end].strip()
    if body.lower() in {"", "void"}:
        return ()
    return tuple(part.strip() for part in body.split(",") if part.strip())


def _parameter_name(parameter: str) -> str:
    cleaned = parameter.replace("[", "").replace("]", "").strip()
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", cleaned)
    return "" if match is None else match.group(1)


def _string_value(
    member: OfficialAutomationMember,
    parameter_name: str,
    fixtures: RuntimeFixturePaths,
) -> str:
    lowered = parameter_name.lower()
    if lowered == "moduletype":
        return "FilePathCheckDLL"
    if lowered == "moduledata":
        return "FilePathCheckerModule"
    if "path" in lowered or "filename" in lowered:
        if member.name in {"SaveAs"}:
            return str(fixtures.output_hwp)
        if member.name in {"CreatePageImage", "GetCtrlToPicture", "SetBarCodeImage"}:
            return str(fixtures.output_image)
        if member.name in {"InsertPicture", "InsertBackgroundPicture"}:
            return str(fixtures.input_image)
        return str(fixtures.input_hwp)
    if lowered in {"format", "arg"}:
        return "HWP" if lowered == "format" else "lock:FALSE"
    if "action" in lowered or lowered == "actname":
        return "Cancel" if member.name == "Run" else "InsertText"
    if lowered in {"setid", "set_id"}:
        return "CharShape"
    if lowered == "ctrlid":
        return "+pbt"
    if lowered in {"field", "fieldlist", "fieldname", "oldname", "newname"}:
        return "codex-field"
    if lowered in {"tag", "name", "itemid", "groupname"}:
        return "codex-runtime-probe"
    if lowered in {"cur_lang", "curlang"}:
        return "ko"
    if lowered in {"trans_lang", "translang"}:
        return "en"
    if lowered == "protectingchar":
        return "*"
    if lowered == "bordertype":
        return "paper"
    return "codex-runtime-probe"


def _integer_value(owner: str, parameter_name: str) -> int:
    lowered = parameter_name.lower()
    if "year" in lowered:
        return 2024
    if "month" in lowered or lowered.endswith("day"):
        return 1
    if "width" in lowered or "height" in lowered:
        return 10
    if lowered == "count":
        return 1
    if lowered == "index" and owner == "IDHwpParameterArray":
        return 1
    return 0


def _action_lines(
    name: str,
    fixtures: RuntimeFixturePaths,
) -> tuple[str, ...]:
    if name == "AQcommandMerge":
        file_name = fixtures.output_hwp.with_name("probe-user-qcommand.dat")
        return (
            "SET\tSave\tI4\t1",
            f"SET\tFileName\tBSTR\t{file_name}",
            "SET\tLoadType\tI4\t1",
        )
    if name == "ConvertOptGugyulToHangul":
        return (
            "FIXTURE\tTEXT_SELECTION\t口訣",
            "SET\tGu\tUI4\t1",
        )
    if name == "ConvertOptHanjaToHangul":
        return (
            "FIXTURE\tTEXT_SELECTION\t漢字",
            "SET\tHanja\tUI4\t1",
            "SET\tHanjaHangul\tUI4\t0",
        )
    if name == "ConvertOptHanjaToHanjaHangul":
        return (
            "FIXTURE\tTEXT_SELECTION\t漢字",
            "SET\tHanja\tUI4\t0",
            "SET\tHanjaHangul\tUI4\t1",
        )
    if name == "PictureChange":
        return (
            f"FIXTURE\tPICTURE_CHANGE\t{fixtures.input_image}",
            f"SET\tPicturePath\tBSTR\t{fixtures.input_image}",
            "SET\tPictureEmbed\tUI1\t1",
        )
    if name == "SaveHistoryItem":
        return (
            "FIXTURE\tMODIFY_TEXT\tCodex official API history probe",
            "SET\tItemOverWrite\tUI1\t0",
            "SET\tItemSaveDescription\tUI1\t0",
            "SET\tVersionAutoSave\tUI1\t1",
            "SET\tItemInfoWriter\tBSTR\tCodex",
            "SET\tItemInfoDescription\tBSTR\tofficial API live probe",
        )
    return ()


def _dispatch_argument(
    owner: str,
    member: str,
    parameter_name: str,
    fixtures: RuntimeFixturePaths,
) -> str:
    if owner in {"IDHwpParameterSet", "IDHwpParameterArray"}:
        return "ARG\tTARGET"
    if owner in {"HAction", "IDHwpAction"}:
        return "ARG\tACTION_SET\tInsertText"
    if member in {"SetPosBySet", "GetSelectedPosBySet"}:
        return "ARG\tPARAMETER_SET\tListParaPos"
    if member in {"ExportStyle", "ImportStyle"}:
        style_path = fixtures.output_hwp.with_name("probe-style.sty")
        return (
            "ARG\tPARAMETER_SET_BSTR\tStyleTemplate\tFileName\t"
            f"{_encoded(str(style_path))}"
        )
    if member == "ReleaseAction":
        return "ARG\tDISPATCH_OWNER\tIDHwpAction"
    if "ctrl" in parameter_name.lower():
        return "ARG\tDISPATCH_OWNER\tIDHwpCtrlCode"
    return "ARG\tTARGET"


def _argument_line(
    member: OfficialAutomationMember,
    parameter: str,
    fixtures: RuntimeFixturePaths,
    safety: OfficialApiSafety,
) -> str:
    optional = parameter.lstrip().startswith("[")
    cleaned = parameter.replace("[", "").replace("]", "").strip()
    parameter_name = _parameter_name(cleaned)
    normalized = " ".join(cleaned.replace("FAR", "").split())
    upper = normalized.upper()
    if optional:
        return "ARG\tMISSING"
    if re.search(r"\bBSTR\s*\*", upper):
        return "ARG\tBYREF_BSTR"
    if re.search(r"\bVARIANT_BOOL\s*\*", upper):
        return "ARG\tBYREF_BOOL"
    if re.search(r"\bSHORT\s*\*", upper):
        return "ARG\tBYREF_I2"
    if re.search(r"\b(?:LONG|INT)\s*\*", upper):
        return "ARG\tBYREF_I4"
    if "LPDISPATCH" in upper or "PARAMETERSET" in upper:
        return _dispatch_argument(
            member.owner,
            member.name,
            parameter_name,
            fixtures,
        )
    if "HWPCTRLCODE" in upper or upper.startswith("CTRL "):
        return "ARG\tDISPATCH_OWNER\tIDHwpCtrlCode"
    if "BSTR" in upper or "LPCTSTR" in upper:
        value = _string_value(member, parameter_name, fixtures)
        return f"ARG\tBSTR64\t{_encoded(value)}"
    if "VARIANT_BOOL" in upper or re.search(r"\b(?:BOOL|BOOLEAN)\b", upper):
        if safety.destructive:
            if _true_is_fail_safe_for_dirty_document(member, parameter_name):
                return "ARG\tBOOL\t1"
            return f"ARG\tUNSUPPORTED\t{_DESTRUCTIVE_BOOL_FAIL_SAFE_UNVERIFIED}"
        return "ARG\tBOOL\t0"
    if re.search(r"\bSHORT\b", upper):
        return f"ARG\tI2\t{_integer_value(member.owner, parameter_name)}"
    if re.search(r"\b(?:UNSIGNED\s+(?:INT|LONG)|ULONG|UINT)\b", upper):
        return f"ARG\tUI4\t{_integer_value(member.owner, parameter_name)}"
    if "VARIANT" in upper:
        if safety.destructive and "dirty" in parameter_name.casefold():
            if _true_is_fail_safe_for_dirty_document(member, parameter_name):
                return "ARG\tBOOL\t1"
            return f"ARG\tUNSUPPORTED\t{_DESTRUCTIVE_BOOL_FAIL_SAFE_UNVERIFIED}"
        if parameter_name.lower() in {"format", "arg", "data"}:
            value = _string_value(member, parameter_name, fixtures)
            return f"ARG\tBSTR64\t{_encoded(value)}"
        return f"ARG\tI4\t{_integer_value(member.owner, parameter_name)}"
    return f"ARG\tI4\t{_integer_value(member.owner, parameter_name)}"


def _true_is_fail_safe_for_dirty_document(
    member: OfficialAutomationMember,
    parameter_name: str,
) -> bool:
    if "dirty" not in parameter_name.casefold():
        return False
    details = re.sub(r"\s+", "", member.details.casefold())
    documents_stay_open = (
        "true" in details or "ture" in details
    ) and "닫지않" in details
    saves_only_when_dirty = "true" in details and (
        "변경된경우에만저장" in details or "변경된경우저장" in details
    )
    return documents_stay_open or saves_only_when_dirty


def _automation_request(
    case: OfficialApiRuntimeCase,
    member: OfficialAutomationMember,
    fixtures: RuntimeFixturePaths,
) -> OfficialApiNativeRequest:
    declaration = member.declaration or _MANUAL_DECLARATIONS.get(member.name, "")
    safety = classify_official_automation_safety(member)
    if member.owner == "IHwpObject" and member.name == "SetCurFieldName":
        lines = (
            f"ARG\tBSTR64\t{_encoded('codex-field')}",
            "ARG\tI2\t0",
            f"ARG\tBSTR64\t{_encoded('codex-direction')}",
            f"ARG\tBSTR64\t{_encoded('codex-memo')}",
        )
    else:
        parameters: list[str] = []
        for parameter in _parameter_text(declaration):
            if parameter.lstrip().startswith("["):
                break
            parameters.append(parameter)
        lines = tuple(
            _argument_line(member, parameter, fixtures, safety)
            for parameter in parameters
        )
    if any(line.startswith("ARG\tUNSUPPORTED\t") for line in lines):
        safety = replace(
            safety,
            arguments_fail_safe=False,
            argument_safety_issue="destructive_bool_fail_safe_unverified",
            rationale=(
                safety.rationale
                + "; destructive boolean semantics have no catalog-proven fail-safe value"
            ),
        )
    payload_lines = [
        "HCV1",
        f"AUTOMATION\t{member.owner}\t{member.name}\t{member.member_kind}",
        _LEGACY_OWNER_FIXTURE_DIRECTIVE,
        *lines,
        "END",
    ]
    return OfficialApiNativeRequest(
        case_id=case.case_id,
        category=case.category,
        name=case.name,
        owner=case.owner,
        member_kind=case.member_kind,
        source_page=case.source_page,
        payload="\n".join(payload_lines),
        input_lines=lines,
        safety=safety,
    )


def build_official_api_requests(
    catalog: OfficialApiCatalog,
    fixtures: RuntimeFixturePaths | None = None,
) -> tuple[OfficialApiNativeRequest, ...]:
    resolved_fixtures = fixtures or RuntimeFixturePaths.defaults()
    runtime_cases = build_runtime_cases(catalog)
    action_map = _action_parameter_map(catalog)
    action_safety = {
        action.name: classify_official_action_safety(action)
        for action in catalog.actions
    }
    result: list[OfficialApiNativeRequest] = []
    action_count = len(catalog.actions)
    parameter_count = len(catalog.parameter_sets)
    for index, case in enumerate(runtime_cases):
        if case.category == "action":
            action = catalog.actions[index]
            input_lines = _action_lines(case.name, resolved_fixtures)
            payload = "\n".join(("HCV1", f"ACTION\t{case.name}", *input_lines, "END"))
            result.append(
                OfficialApiNativeRequest(
                    case_id=case.case_id,
                    category=case.category,
                    name=case.name,
                    owner=None,
                    member_kind=None,
                    source_page=case.source_page,
                    payload=payload,
                    input_lines=input_lines,
                    safety=classify_official_action_safety(action),
                )
            )
            continue
        if case.category == "parameter_set":
            parameter_set = catalog.parameter_sets[index - action_count]
            linked_action = action_map.get(parameter_set.name, ("-",))[0]
            linked_safety = action_safety.get(linked_action, _SAFE_CATALOG_OPERATION)
            parameter_safety = (
                replace(
                    linked_safety,
                    classification="inherited_catalog_metadata",
                    rationale=(
                        "parameter-set execution inherits the linked catalog "
                        f"action effect: {linked_action}"
                    ),
                )
                if linked_action in action_safety
                else _SAFE_CATALOG_OPERATION
            )
            item_lines = tuple(
                f"ITEM\t{item.name}\t{item.value_type}\t{item.subtype or '-'}"
                for item in parameter_set.items
            )
            payload = "\n".join(
                (
                    "HCV1",
                    f"PARAMETER_SET\t{parameter_set.name}\t{linked_action}",
                    *item_lines,
                    "END",
                )
            )
            result.append(
                OfficialApiNativeRequest(
                    case_id=case.case_id,
                    category=case.category,
                    name=case.name,
                    owner=None,
                    member_kind=None,
                    source_page=case.source_page,
                    payload=payload,
                    input_lines=item_lines,
                    safety=parameter_safety,
                )
            )
            continue
        automation_index = index - action_count - parameter_count
        result.append(
            _automation_request(
                case,
                catalog.automation_members[automation_index],
                resolved_fixtures,
            )
        )
    return tuple(result)
