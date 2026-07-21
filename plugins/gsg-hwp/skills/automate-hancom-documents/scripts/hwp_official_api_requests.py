from __future__ import annotations

import base64
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from hwp_official_api_contract import (
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
        return "ARG\tBOOL\t0"
    if re.search(r"\bSHORT\b", upper):
        return f"ARG\tI2\t{_integer_value(member.owner, parameter_name)}"
    if re.search(r"\b(?:UNSIGNED\s+(?:INT|LONG)|ULONG|UINT)\b", upper):
        return f"ARG\tUI4\t{_integer_value(member.owner, parameter_name)}"
    if "VARIANT" in upper:
        if parameter_name.lower() in {"format", "arg", "data"}:
            value = _string_value(member, parameter_name, fixtures)
            return f"ARG\tBSTR64\t{_encoded(value)}"
        return f"ARG\tI4\t{_integer_value(member.owner, parameter_name)}"
    return f"ARG\tI4\t{_integer_value(member.owner, parameter_name)}"


def _automation_request(
    case: OfficialApiRuntimeCase,
    member: OfficialAutomationMember,
    fixtures: RuntimeFixturePaths,
) -> OfficialApiNativeRequest:
    declaration = member.declaration or _MANUAL_DECLARATIONS.get(member.name, "")
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
            _argument_line(member, parameter, fixtures)
            for parameter in parameters
        )
    payload_lines = [
        "HCV1",
        f"AUTOMATION\t{member.owner}\t{member.name}\t{member.member_kind}",
        "FIXTURE\tISOLATED",
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
    )


def build_official_api_requests(
    catalog: OfficialApiCatalog,
    fixtures: RuntimeFixturePaths | None = None,
) -> tuple[OfficialApiNativeRequest, ...]:
    resolved_fixtures = fixtures or RuntimeFixturePaths.defaults()
    runtime_cases = build_runtime_cases(catalog)
    action_map = _action_parameter_map(catalog)
    result: list[OfficialApiNativeRequest] = []
    action_count = len(catalog.actions)
    parameter_count = len(catalog.parameter_sets)
    for index, case in enumerate(runtime_cases):
        if case.category == "action":
            input_lines = _action_lines(case.name, resolved_fixtures)
            payload = "\n".join(
                ("HCV1", f"ACTION\t{case.name}", *input_lines, "END")
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
                    input_lines=input_lines,
                )
            )
            continue
        if case.category == "parameter_set":
            parameter_set = catalog.parameter_sets[index - action_count]
            linked_action = action_map.get(parameter_set.name, ("-",))[0]
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
