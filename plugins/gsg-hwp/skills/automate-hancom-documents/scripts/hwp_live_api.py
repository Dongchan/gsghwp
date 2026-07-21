from __future__ import annotations

from typing import Literal, Protocol

from hwp_runtime import PictureControl


ShapeValue = str | int | float | bool | None
Alignment = Literal["Justify", "Left", "Center", "Right"]
SelectionRange = tuple[
    bool,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
]


class HwpControl(Protocol):
    @property
    def CtrlID(self) -> str: ...

    @property
    def UserDesc(self) -> str: ...

    def GetCtrlInstID(self) -> str: ...

    def GetAnchorPos(self, option: int) -> HwpPositionSet: ...


class HwpPositionSet(Protocol):
    def Item(self, name: str) -> int: ...


class HwpDocumentInfo(Protocol):
    @property
    def CurrentPage(self) -> int: ...


class HwpComDocument(Protocol):
    @property
    def DocumentID(self) -> int: ...

    @property
    def FullName(self) -> str: ...

    @property
    def Format(self) -> str: ...

    @property
    def EditMode(self) -> int: ...

    @property
    def Modified(self) -> int: ...

    @property
    def XHwpDocumentInfo(self) -> HwpDocumentInfo: ...

    def SetActive_XHwpDocument(self) -> None: ...

class HwpDocuments(Protocol):
    @property
    def Count(self) -> int: ...

    @property
    def Active_XHwpDocument(self) -> HwpComDocument: ...

    def Item(self, index: int) -> HwpComDocument: ...

    def FindItem(self, document_id: int) -> HwpComDocument | None: ...


class HwpWindow(Protocol):
    @property
    def WindowHandle(self) -> int: ...


class HwpWindows(Protocol):
    @property
    def Active_XHwpWindow(self) -> HwpWindow: ...


class HwpRegistrar(Protocol):
    def RegisterModule(self, *, ModuleType: str, ModuleData: str) -> bool: ...


class HwpSet(Protocol):
    def SetItem(self, name: str, value: int) -> None: ...


class HwpCellBorderFill(Protocol):
    @property
    def HSet(self) -> HwpSet: ...


class HwpParameterSets(Protocol):
    @property
    def HCellBorderFill(self) -> HwpCellBorderFill: ...


class HwpAction(Protocol):
    def Run(self, action: str) -> bool: ...

    def GetDefault(self, action: str, parameters: HwpSet) -> bool: ...

    def Execute(self, action: str, parameters: HwpSet) -> bool: ...


class HwpComApplication(HwpRegistrar, Protocol):
    @property
    def XHwpDocuments(self) -> HwpDocuments: ...

    @property
    def XHwpWindows(self) -> HwpWindows: ...

    @property
    def PageCount(self) -> int: ...

    def Run(self, action: str) -> bool: ...

    def GetTextFile(self, format: str, option: str) -> str: ...

    def SetTextFile(self, data: str, format: str, option: str) -> bool: ...

class LiveHwpApplication(Protocol):
    hwp: HwpComApplication
    on_quit: bool
    htf_fonts: dict[str, dict[str, int | str]]

    @property
    def PageCount(self) -> int: ...

    @property
    def Version(self) -> list[int]: ...

    @property
    def current_page(self) -> int: ...

    @property
    def IsModified(self) -> bool: ...

    @property
    def SelectionMode(self) -> int: ...

    @property
    def ctrl_list(self) -> list[HwpControl]: ...

    @property
    def ParentCtrl(self) -> HwpControl: ...

    @property
    def CurSelectedCtrl(self) -> HwpControl: ...

    @property
    def HAction(self) -> HwpAction: ...

    @property
    def HParameterSet(self) -> HwpParameterSets: ...

    def get_pos(self) -> tuple[int, int, int]: ...

    def set_pos(self, List: int, para: int, pos: int) -> bool: ...

    def select_text(self, spara: SelectionRange) -> bool: ...

    def get_selected_pos(self) -> SelectionRange: ...

    def get_ctrl_pos(
        self,
        ctrl: HwpControl,
        option: Literal[0, 1] = 0,
        as_tuple: bool = True,
    ) -> tuple[int, int, int]: ...

    def goto_page(self, page_index: int | str = 1) -> tuple[int, int]: ...

    def move_to_ctrl(self, ctrl: HwpControl) -> bool: ...

    def SelectCtrl(
        self,
        ctrllist: HwpControl,
        option: Literal[0, 1] = 1,
    ) -> bool: ...

    def FindCtrl(self) -> bool: ...

    def ShapeObjTextBoxEdit(self) -> bool: ...

    def goto_addr(
        self,
        addr: str = "A1",
        col: int = 0,
        select_cell: bool = False,
    ) -> bool: ...

    def get_cell_addr(self) -> str: ...

    def is_cell(self) -> bool: ...

    def RecalcPageCount(self) -> bool: ...

    def get_text_file(
        self,
        format: Literal["UNICODE", "HWPML2X"] = "UNICODE",
        option: str = "saveblock:true",
    ) -> str: ...

    def get_page_text(self, pgno: int = 0, option: int = 0xFFFFFFFF) -> str: ...

    def get_charshape_as_dict(self) -> dict[str, ShapeValue]: ...

    def get_parashape_as_dict(self) -> dict[str, ShapeValue]: ...

    def get_pagedef_as_dict(
        self,
        as_: Literal["eng"] = "eng",
    ) -> dict[str, ShapeValue]: ...

    def MoveSelRight(self) -> bool: ...

    def MoveSelLeft(self) -> bool: ...

    def set_style(self, style: int | str) -> bool: ...

    def insert_text(self, text: str) -> bool: ...

    def BreakPara(self) -> bool: ...

    def BreakPage(self) -> bool: ...

    def MoveParentList(self) -> bool: ...

    def MoveParaBegin(self) -> bool: ...

    def MoveParaEnd(self) -> bool: ...

    def MoveListEnd(self) -> bool: ...

    def MoveSelParaEnd(self) -> bool: ...

    def set_font(
        self,
        Bold: bool | str = "",
        FaceName: str = "",
        Height: float | str = "",
        TextColor: int | str = "",
    ) -> bool: ...

    def set_para(
        self,
        AlignType: Alignment | None = None,
        LineSpacing: int | None = None,
        NextSpacing: float | None = None,
        PrevSpacing: float | None = None,
        Indentation: float | None = None,
        RightMargin: float | None = None,
        LeftMargin: float | None = None,
    ) -> bool: ...

    def create_table(
        self,
        rows: int = 1,
        cols: int = 1,
        treat_as_char: bool = True,
        width_type: int = 0,
        height_type: int = 0,
        header: bool = True,
        height: int = 0,
    ) -> bool: ...

    def set_col_width(
        self,
        width: tuple[float, ...],
        as_: Literal["mm"] = "mm",
    ) -> bool: ...

    def set_row_height(
        self,
        height: float,
        as_: Literal["mm"] = "mm",
    ) -> bool: ...

    def TableRightCell(self) -> bool: ...

    def TableLowerCell(self) -> bool: ...

    def TableColBegin(self) -> bool: ...

    def TableColPageUp(self) -> bool: ...

    def TableCellBlock(self) -> bool: ...

    def TableCellBlockExtend(self) -> bool: ...

    def TableMergeCell(self) -> bool: ...

    def TableVAlignTop(self) -> bool: ...

    def TableVAlignCenter(self) -> bool: ...

    def TableVAlignBottom(self) -> bool: ...

    def SelectAll(self) -> bool: ...

    def Cancel(self) -> bool: ...

    def Delete(self) -> bool: ...

    def CloseEx(self) -> bool: ...

    def SelectCtrlFront(self) -> bool: ...

    def ShapeObjAttachCaption(self, text: str = "", add_num: bool = True) -> bool: ...

    def ShapeObjInsertCaptionNum(self) -> bool: ...

    def set_cell_margin(
        self,
        left: float = 1.8,
        right: float = 1.8,
        top: float = 0.5,
        bottom: float = 0.5,
        as_: Literal["mm"] = "mm",
    ) -> bool: ...

    def HwpLineType(self, line_type: str) -> int: ...

    def HwpLineWidth(self, line_width: str) -> int: ...

    def cell_fill(self, face_color: tuple[int, int, int]) -> bool: ...

    def insert_picture(
        self,
        path: str,
        treat_as_char: bool = True,
        embedded: bool = True,
        sizeoption: int = 0,
        reverse: bool = False,
        watermark: bool = False,
        effect: int = 0,
        width: int = 0,
        height: int = 0,
    ) -> PictureControl | None: ...

    def MiliToHwpUnit(self, value: float) -> float: ...

    def create_page_image(
        self,
        path: str,
        pgno: int = -1,
        resolution: int = 300,
        depth: int = 24,
        format: str = "bmp",
    ) -> bool: ...
