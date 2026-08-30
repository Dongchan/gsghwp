#pragma once

#include "DocumentGraphTypes.h"

#include <array>
#include <cstddef>
#include <cstdint>

namespace hancom::graph {

inline constexpr std::uint16_t kSchemaVersionV1 = 1;
inline constexpr std::uint16_t kLogicalRecordHeaderBytesV1 = 24;
inline constexpr std::uint16_t kFieldHeaderBytesV1 = 24;
inline constexpr std::uint64_t kCanonicalAssetChunkBytesV1 = 1'048'576;
inline constexpr std::uint16_t kFieldFlagRequired = 0x0001;
inline constexpr std::uint16_t kFieldFlagArray = 0x0002;
inline constexpr std::uint16_t kReservedCommonFieldTagV1 = 9;
inline constexpr std::uint8_t kNoProfile = 255;

struct LogicalRecordHeaderV1 final {
    std::uint16_t recordKind = 0;
    std::uint16_t recordVersion = 1;
    std::uint16_t recordFlags = 0;
    std::uint16_t fieldCount = 0;
    std::uint64_t recordId = 0;
    std::uint64_t payloadBytes = 0;
};
static_assert(sizeof(LogicalRecordHeaderV1) == 24, "LogicalRecordV1 header must be 24 bytes");
struct FieldHeaderV1 final {
    std::uint16_t fieldTag = 0;
    std::uint16_t fieldFlags = 0;
    std::uint16_t scalarTag = 0;
    std::uint16_t reserved = 0;
    std::uint64_t elementCount = 1;
    std::uint64_t valueBytes = 0;
};
static_assert(sizeof(FieldHeaderV1) == 24, "FieldV1 header must be 24 bytes");
struct ArrayHeaderV1 final {
    std::uint16_t elementScalarTag = 0;
    std::uint16_t elementFlags = 0;
    std::uint32_t reserved = 0;
    std::uint64_t count = 0;
};
static_assert(sizeof(ArrayHeaderV1) == 16, "FieldV1 array header must be 16 bytes");
inline constexpr std::uint16_t kRecordVersionV1 = 1;
inline constexpr std::uint16_t kObservationEnvelopeFixedBytesV1 = 24;
inline constexpr std::uint16_t kGraphVersionBytesV1 = 168;

enum class CaptureIntegrity : std::uint8_t {
    Complete = 0, UnknownOmission = 1, Cancelled = 2, RouteChanged = 3,
    StorageFailure = 4, TraversalFailure = 5, StateRestoreFailure = 6,
};
enum class CoverageState : std::uint8_t {
    Complete = 0, NotRequested = 1, NotApplicable = 2, NotExposed = 3,
    ReadFailed = 4, ProjectionOmitted = 5,
};
enum class Writability : std::uint8_t { ReadOnly = 0, Writable = 1 };
enum class ProfileId : std::uint8_t {
    Structure = 0, EditableText = 1, EditableObjects = 2, Layout = 3,
    BinaryContent = 4,
};
enum class RecordKind : std::uint16_t {
    Manifest = 0, Node = 1, Edge = 2, Property = 3, Coverage = 4,
    Diagnostic = 5, AssetChunk = 6, Tombstone = 7, Remap = 8,
};
enum class NodeKind : std::uint16_t {
    Document = 0, Story = 1, Section = 2, Paragraph = 3, CharacterRun = 4,
    SpecialCharacter = 5, GeneratedText = 6, GenericControl = 7, Table = 8,
    TableCell = 9, Image = 10, BinaryData = 11, Definition = 12,
};
enum class DefinitionKind : std::uint16_t {
    Style = 0, CharacterShape = 1, ParagraphShape = 2, Numbering = 3,
    BorderFill = 4, TabDef = 5, PageDef = 6, ColumnDef = 7,
};
enum class StoryKind : std::uint16_t {
    Body = 0, Header = 1, Footer = 2, Footnote = 3, Endnote = 4,
    Caption = 5, TableCell = 6, TextBox = 7, Other = 8,
};
enum class EdgeKind : std::uint16_t {
    Contains = 0, Anchors = 1, StyleRef = 2, NumberingRef = 3,
    BorderFillRef = 4, TabDefRef = 5, AssetRef = 6,
    HeaderFooterApplies = 7, CaptionOf = 8, OwnerStory = 9,
    CharacterShapeRef = 10, ParagraphShapeRef = 11, PageDefRef = 12,
    ColumnDefRef = 13,
};
enum class PropertyDomain : std::uint8_t {
    CharacterShape = 0, ParagraphShape = 1, Style = 2, Numbering = 3,
    BorderFill = 4, TabDef = 5, PageSetup = 6, Section = 7, Control = 8,
    Table = 9, Cell = 10, Image = 11, Layout = 12, NativeExtension = 13,
    DocumentMetadata = 14,
};
enum class ScalarTag : std::uint16_t {
    Sint64 = 0, Uint64 = 1, Bool = 2, Float64 = 3, UTF16 = 4,
    HWPUNIT64 = 5, BGR = 6, Enum = 7, BlobSlice = 8, UUID128 = 9,
    SHA256 = 10, Struct = 11, RawURC32 = 12, Bytes = 13, Uint8 = 14,
    Uint16 = 15, Uint32 = 16, Sint32 = 17,
};
constexpr std::uint8_t FixedScalarBytes(const ScalarTag scalar) noexcept {
    switch (scalar) {
    case ScalarTag::Sint64: case ScalarTag::Uint64: case ScalarTag::Float64:
    case ScalarTag::HWPUNIT64: return 8;
    case ScalarTag::BGR: case ScalarTag::RawURC32: case ScalarTag::Uint32:
    case ScalarTag::Sint32: return 4;
    case ScalarTag::UUID128: return 16;
    case ScalarTag::SHA256: return 32;
    case ScalarTag::Bool: case ScalarTag::Uint8: return 1;
    case ScalarTag::Uint16: return 2;
    default: return 0;
    }
}

enum class PropertyOrigin : std::uint8_t {
    Direct = 0, Inherited = 1, Generated = 2, Unknown = 3,
    UserOverride = 4, LocalStyle = 5, NamedStyle = 6,
    DocumentDefault = 7, ImplicitDefault = 8,
    NotApplicable = 9, Unavailable = 10,
};
enum class DiagnosticCode : std::uint32_t {
    NativeReadFailure = 0, InconsistentProperty = 1, UnknownControl = 2,
    LayoutUnavailable = 3, UnsupportedAdapter = 4, StateRestoreFailure = 5,
    TraversalCorruption = 6,
};
enum class Severity : std::uint8_t { Info = 0, Warning = 1, Error = 2 };
enum class TombstoneReason : std::uint16_t {
    Delete = 0, MergeAbsorbed = 1, Coalesce = 2, ExternalUnmatched = 3,
    ReopenNewLineage = 4,
};
enum class RemapDisposition : std::uint8_t {
    Retained = 0, New = 1, Tombstoned = 2, Ambiguous = 3,
};
enum class RemapReason : std::uint16_t {
    IdentityRetained = 0, ClientInsert = 1, Split = 2, Coalesce = 3,
    Move = 4, Merge = 5, TableCellSplit = 6, Clone = 7,
    SaveReopenEqual = 8, ExternalUnique = 9, Ambiguous = 10, Delete = 11,
};
enum class RootClass : std::uint8_t { None = 0, Semantic = 1, Layout = 2, Capture = 3 };
enum class Requirement : std::uint8_t { Required = 0, Optional = 1 };
enum class PropertyCollectionRule : std::uint8_t {
    SortedUniquePropertyKey = 0,
    RequiredEmptyWhenProfileAbsent = 1,
};

constexpr std::uint64_t ProfileBit(const ProfileId profile) noexcept {
    return std::uint64_t{1} << static_cast<std::uint8_t>(profile);
}
inline constexpr std::uint64_t kKnownProfileBits =
    ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::EditableText) |
    ProfileBit(ProfileId::EditableObjects) | ProfileBit(ProfileId::Layout) |
    ProfileBit(ProfileId::BinaryContent);

constexpr bool HasOnlyKnownProfiles(const std::uint64_t bits) noexcept {
    return (bits & ~kKnownProfileBits) == 0;
}
constexpr std::uint64_t CloseProfileBits(std::uint64_t bits) noexcept {
    if ((bits & ProfileBit(ProfileId::EditableObjects)) != 0) {
        bits |= ProfileBit(ProfileId::EditableText);
    }
    if ((bits & (ProfileBit(ProfileId::EditableText) |
                 ProfileBit(ProfileId::Layout) |
                 ProfileBit(ProfileId::BinaryContent))) != 0) {
        bits |= ProfileBit(ProfileId::Structure);
    }
    return bits;
}

struct ProfileRule final { ProfileId profile; std::uint64_t dependencyBits; };
inline constexpr std::array<ProfileRule, 5> kProfileRules{{
    {ProfileId::Structure,0},
    {ProfileId::EditableText,ProfileBit(ProfileId::Structure)},
    {ProfileId::EditableObjects,ProfileBit(ProfileId::EditableText)|ProfileBit(ProfileId::Structure)},
    {ProfileId::Layout,ProfileBit(ProfileId::Structure)},
    {ProfileId::BinaryContent,ProfileBit(ProfileId::Structure)},
}};
enum class CoverageCoordinateKind : std::uint8_t {
    Global = 0, Profile = 1, Node = 2, NodeField = 3, Property = 4,
};
struct CoverageCoordinateRule final {
    CoverageCoordinateKind kind;
    bool ownerPresent;
    std::uint8_t profile;
    RecordKind ownerRecordKind;
    FieldTag ownerFieldTag;
    bool propertyKeyPresent;
};
inline constexpr std::array<CoverageCoordinateRule, 5> kCoverageCoordinateRules{{
    {CoverageCoordinateKind::Global,false,kNoProfile,RecordKind::Manifest,0,false},
    {CoverageCoordinateKind::Profile,false,0,RecordKind::Coverage,0,false},
    {CoverageCoordinateKind::Node,true,kNoProfile,RecordKind::Node,0,false},
    {CoverageCoordinateKind::NodeField,true,kNoProfile,RecordKind::Node,0,false},
    {CoverageCoordinateKind::Property,true,0,RecordKind::Property,0,true},
}};
constexpr bool IsCoverageCoordinate(
    const CoverageCoordinateKind kind,
    const bool ownerPresent,
    const std::uint8_t profile,
    const RecordKind ownerRecordKind,
    const FieldTag ownerFieldTag,
    const bool propertyKeyPresent) noexcept {
    const bool actualProfile =
        profile <= static_cast<std::uint8_t>(ProfileId::BinaryContent);
    switch (kind) {
    case CoverageCoordinateKind::Global:
        return !ownerPresent && profile == kNoProfile &&
            ownerRecordKind == RecordKind::Manifest && ownerFieldTag == 0 &&
            !propertyKeyPresent;
    case CoverageCoordinateKind::Profile:
        return !ownerPresent && actualProfile &&
            ownerRecordKind == RecordKind::Coverage &&
            ownerFieldTag == profile && !propertyKeyPresent;
    case CoverageCoordinateKind::Node:
        return ownerPresent && profile == kNoProfile &&
            ownerRecordKind == RecordKind::Node && ownerFieldTag == 0 &&
            !propertyKeyPresent;
    case CoverageCoordinateKind::NodeField:
        return ownerPresent && (profile == kNoProfile || actualProfile) &&
            ownerRecordKind == RecordKind::Node && ownerFieldTag != 0 &&
            !propertyKeyPresent;
    case CoverageCoordinateKind::Property:
        return ownerPresent && actualProfile &&
            ownerRecordKind == RecordKind::Property && ownerFieldTag != 0 &&
            propertyKeyPresent;
    }
    return false;
}
struct PropertyRecordIdentity final {
    NodeId owner{};
    FieldTag ownerNodeFieldTag = 0;
    PropertyKeyId propertyKey = 0;
};

struct RecordFieldRule final {
    RecordKind record;
    FieldTag tag;
    ScalarTag scalar;
    Requirement requirement;
};

inline constexpr std::array<RecordFieldRule, 42> kRecordFieldRules{{
    {RecordKind::Manifest,1,ScalarTag::Struct,Requirement::Required},
    {RecordKind::Manifest,2,ScalarTag::Uint8,Requirement::Required},
    {RecordKind::Manifest,3,ScalarTag::Uint64,Requirement::Required},
    {RecordKind::Manifest,4,ScalarTag::Uint64,Requirement::Required},
    {RecordKind::Manifest,5,ScalarTag::SHA256,Requirement::Required},
    {RecordKind::Manifest,6,ScalarTag::Uint8,Requirement::Required},
    {RecordKind::Node,1,ScalarTag::Struct,Requirement::Required},
    {RecordKind::Node,2,ScalarTag::Struct,Requirement::Required},
    {RecordKind::Edge,1,ScalarTag::Uint16,Requirement::Required},
    {RecordKind::Edge,2,ScalarTag::UUID128,Requirement::Required},
    {RecordKind::Edge,3,ScalarTag::UUID128,Requirement::Required},
    {RecordKind::Edge,4,ScalarTag::Uint64,Requirement::Required},
    {RecordKind::Property,1,ScalarTag::UUID128,Requirement::Required},
    {RecordKind::Property,2,ScalarTag::Uint16,Requirement::Required},
    {RecordKind::Property,3,ScalarTag::Uint32,Requirement::Required},
    {RecordKind::Property,4,ScalarTag::Struct,Requirement::Required},
    {RecordKind::Property,5,ScalarTag::Uint8,Requirement::Required},
    {RecordKind::Coverage,1,ScalarTag::UUID128,Requirement::Optional},
    {RecordKind::Coverage,2,ScalarTag::Uint8,Requirement::Required},
    {RecordKind::Coverage,3,ScalarTag::Uint8,Requirement::Required},
    {RecordKind::Coverage,4,ScalarTag::Uint32,Requirement::Optional},
    {RecordKind::Coverage,5,ScalarTag::BlobSlice,Requirement::Required},
    {RecordKind::Coverage,6,ScalarTag::Uint16,Requirement::Required},
    {RecordKind::Coverage,7,ScalarTag::Uint16,Requirement::Required},
    {RecordKind::Diagnostic,1,ScalarTag::Uint32,Requirement::Required},
    {RecordKind::Diagnostic,2,ScalarTag::Uint8,Requirement::Required},
    {RecordKind::Diagnostic,3,ScalarTag::UUID128,Requirement::Optional},
    {RecordKind::Diagnostic,4,ScalarTag::Uint32,Requirement::Optional},
    {RecordKind::Diagnostic,5,ScalarTag::Sint32,Requirement::Required},
    {RecordKind::Diagnostic,6,ScalarTag::BlobSlice,Requirement::Required},
    {RecordKind::AssetChunk,1,ScalarTag::UUID128,Requirement::Required},
    {RecordKind::AssetChunk,2,ScalarTag::Uint64,Requirement::Required},
    {RecordKind::AssetChunk,3,ScalarTag::Uint64,Requirement::Required},
    {RecordKind::AssetChunk,4,ScalarTag::Bytes,Requirement::Required},
    {RecordKind::AssetChunk,5,ScalarTag::SHA256,Requirement::Required},
    {RecordKind::Tombstone,1,ScalarTag::UUID128,Requirement::Required},
    {RecordKind::Tombstone,2,ScalarTag::Uint64,Requirement::Required},
    {RecordKind::Tombstone,3,ScalarTag::Uint16,Requirement::Required},
    {RecordKind::Remap,1,ScalarTag::UUID128,Requirement::Required},
    {RecordKind::Remap,2,ScalarTag::UUID128,Requirement::Optional},
    {RecordKind::Remap,3,ScalarTag::Uint8,Requirement::Required},
    {RecordKind::Remap,4,ScalarTag::Uint16,Requirement::Required},
}};

enum class FieldProfileBinding : std::uint8_t { Fixed = 0, DefinitionOwning = 1 };
struct NodeFieldRule final {
    NodeKind node;
    FieldTag tag;
    ScalarTag scalar;
    Requirement requirement;
    ProfileId profile;
    FieldProfileBinding profileBinding;
    RootClass root;
    bool observation;
    bool array;
};
#define HWP_GRAPH_FIELD(node, tag, scalar, req, profile, root, obs, array) \
    NodeFieldRule{NodeKind::node, tag, ScalarTag::scalar, Requirement::req, \
                  ProfileId::profile, FieldProfileBinding::Fixed, RootClass::root, obs, array}
inline constexpr std::array<NodeFieldRule, 10> kCommonNodeFieldRules{{
    HWP_GRAPH_FIELD(Document,1,UUID128,Required,Structure,None,false,false),
    HWP_GRAPH_FIELD(Document,2,Uint16,Required,Structure,Semantic,false,false),
    HWP_GRAPH_FIELD(Document,3,UUID128,Optional,Structure,Semantic,false,false),
    HWP_GRAPH_FIELD(Document,4,Uint64,Required,Structure,Semantic,false,false),
    HWP_GRAPH_FIELD(Document,5,Uint64,Required,Structure,None,false,false),
    HWP_GRAPH_FIELD(Document,6,Uint64,Required,Structure,None,false,false),
    HWP_GRAPH_FIELD(Document,7,Struct,Required,Structure,Capture,true,false),
    HWP_GRAPH_FIELD(Document,8,SHA256,Required,Structure,None,false,false),
    HWP_GRAPH_FIELD(Document,10,Uint64,Required,Layout,Layout,false,true),
    HWP_GRAPH_FIELD(Document,11,Uint64,Required,BinaryContent,Capture,false,true),
}};
inline constexpr std::array<NodeFieldRule, 60> kNodePayloadFieldRules{{
    HWP_GRAPH_FIELD(Document,100,Uint64,Required,Structure,Semantic,false,true),
    HWP_GRAPH_FIELD(Story,100,Uint16,Required,Structure,Semantic,false,false),
    HWP_GRAPH_FIELD(Story,101,UUID128,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(Story,102,Sint64,Required,Structure,Capture,true,false),
    HWP_GRAPH_FIELD(Story,103,Bool,Optional,EditableText,Semantic,true,false),
    HWP_GRAPH_FIELD(Story,104,Sint64,Optional,EditableText,Capture,true,false),
    HWP_GRAPH_FIELD(Story,105,UTF16,Optional,EditableText,Capture,true,false),
    HWP_GRAPH_FIELD(Section,100,Uint64,Required,Structure,Semantic,false,false),
    HWP_GRAPH_FIELD(Section,101,Struct,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(Section,102,Struct,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(Section,103,Uint64,Required,Structure,Semantic,false,true),
    HWP_GRAPH_FIELD(Paragraph,100,Struct,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(Paragraph,101,Struct,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(Paragraph,102,Sint64,Required,EditableText,Capture,true,false),
    HWP_GRAPH_FIELD(Paragraph,103,Uint64,Required,EditableText,Semantic,false,true),
    HWP_GRAPH_FIELD(Paragraph,104,Uint64,Required,EditableText,Semantic,false,true),
    HWP_GRAPH_FIELD(CharacterRun,100,Struct,Required,EditableText,Semantic,false,false),
    HWP_GRAPH_FIELD(CharacterRun,101,BlobSlice,Required,EditableText,Semantic,true,false),
    HWP_GRAPH_FIELD(CharacterRun,102,Uint64,Required,EditableText,Semantic,false,true),
    HWP_GRAPH_FIELD(SpecialCharacter,100,Struct,Required,EditableText,Semantic,false,false),
    HWP_GRAPH_FIELD(SpecialCharacter,101,Sint32,Required,EditableText,Semantic,false,false),
    HWP_GRAPH_FIELD(SpecialCharacter,102,BlobSlice,Required,EditableText,Semantic,true,false),
    HWP_GRAPH_FIELD(SpecialCharacter,103,Bool,Required,EditableText,Semantic,false,false),
    HWP_GRAPH_FIELD(GeneratedText,100,Struct,Required,EditableText,Semantic,true,false),
    HWP_GRAPH_FIELD(GeneratedText,101,Sint32,Required,EditableText,Semantic,false,false),
    HWP_GRAPH_FIELD(GeneratedText,102,BlobSlice,Required,EditableText,Semantic,true,false),
    HWP_GRAPH_FIELD(GenericControl,100,UTF16,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(GenericControl,101,UTF16,Required,Structure,Capture,true,false),
    HWP_GRAPH_FIELD(GenericControl,102,UTF16,Required,Structure,Capture,true,false),
    HWP_GRAPH_FIELD(GenericControl,103,Struct,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(GenericControl,104,Struct,Required,Structure,Capture,true,false),
    HWP_GRAPH_FIELD(GenericControl,105,Uint64,Required,Structure,Semantic,false,false),
    HWP_GRAPH_FIELD(GenericControl,106,Uint64,Required,EditableObjects,Semantic,false,true),
    HWP_GRAPH_FIELD(Table,107,Uint64,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(Table,108,Uint64,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(Table,109,SHA256,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,100,UUID128,Required,Structure,Semantic,false,false),
    HWP_GRAPH_FIELD(TableCell,101,UTF16,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,102,Uint64,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,103,Uint64,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,104,Uint64,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,105,Uint64,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,106,Sint64,Required,Structure,Capture,true,false),
    HWP_GRAPH_FIELD(TableCell,107,UUID128,Required,Structure,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,108,Struct,Required,EditableObjects,Semantic,true,false),
    HWP_GRAPH_FIELD(TableCell,109,Uint64,Required,EditableObjects,Semantic,false,true),
    HWP_GRAPH_FIELD(Image,107,Struct,Required,EditableObjects,Semantic,true,false),
    HWP_GRAPH_FIELD(Image,108,Struct,Required,EditableObjects,Semantic,true,false),
    HWP_GRAPH_FIELD(Image,109,Struct,Required,EditableObjects,Semantic,true,false),
    HWP_GRAPH_FIELD(Image,110,Struct,Required,BinaryContent,Semantic,true,false),
    HWP_GRAPH_FIELD(Image,111,UTF16,Required,BinaryContent,Capture,true,false),
    HWP_GRAPH_FIELD(Image,112,Uint64,Required,EditableObjects,Semantic,false,true),
    HWP_GRAPH_FIELD(BinaryData,100,UUID128,Required,BinaryContent,None,false,false),
    HWP_GRAPH_FIELD(BinaryData,101,Uint64,Required,BinaryContent,Semantic,false,false),
    HWP_GRAPH_FIELD(BinaryData,102,SHA256,Required,BinaryContent,Semantic,false,false),
    HWP_GRAPH_FIELD(BinaryData,103,UTF16,Required,BinaryContent,Capture,true,false),
    {NodeKind::Definition,100,ScalarTag::Uint16,Requirement::Required,ProfileId::Structure,FieldProfileBinding::DefinitionOwning,RootClass::Semantic,false,false},
    {NodeKind::Definition,101,ScalarTag::Sint64,Requirement::Required,ProfileId::Structure,FieldProfileBinding::DefinitionOwning,RootClass::Semantic,true,false},
    {NodeKind::Definition,102,ScalarTag::Uint64,Requirement::Required,ProfileId::Structure,FieldProfileBinding::DefinitionOwning,RootClass::Semantic,false,true},
    {NodeKind::Definition,103,ScalarTag::SHA256,Requirement::Required,ProfileId::Structure,FieldProfileBinding::DefinitionOwning,RootClass::None,false,false},
}};
#undef HWP_GRAPH_FIELD

struct LocatorFieldRule final { LocatorTag locator; FieldTag tag; ScalarTag scalar; };
inline constexpr std::array<LocatorFieldRule, 14> kLocatorFieldRules{{
    {LocatorTag::Story,1,ScalarTag::Sint64},
    {LocatorTag::Paragraph,1,ScalarTag::Sint64},{LocatorTag::Paragraph,2,ScalarTag::Sint64},
    {LocatorTag::Run,1,ScalarTag::Sint64},{LocatorTag::Run,2,ScalarTag::Sint64},
    {LocatorTag::Run,3,ScalarTag::Sint64},{LocatorTag::Run,4,ScalarTag::Sint64},
    {LocatorTag::Control,1,ScalarTag::UTF16},{LocatorTag::Control,2,ScalarTag::UTF16},
    {LocatorTag::Control,3,ScalarTag::Uint64},{LocatorTag::Control,4,ScalarTag::Struct},
    {LocatorTag::Cell,1,ScalarTag::UUID128},{LocatorTag::Cell,2,ScalarTag::UTF16},
    {LocatorTag::Cell,3,ScalarTag::Sint64},
}};

constexpr bool IsControlKind(const NodeKind kind) noexcept {
    return kind == NodeKind::GenericControl || kind == NodeKind::Table || kind == NodeKind::Image;
}
constexpr bool IsLegalPrimaryParent(
    const NodeKind child, const NodeKind parent,
    const StoryKind childStory = StoryKind::Other,
    const StoryKind parentStory = StoryKind::Other) noexcept {
    if (child == NodeKind::Document) return false;
    if (child == NodeKind::BinaryData || child == NodeKind::Definition) return parent == NodeKind::Document;
    if (child == NodeKind::Story) {
        if (childStory == StoryKind::TableCell) return parent == NodeKind::TableCell;
        if (childStory == StoryKind::Body || childStory == StoryKind::Other ||
            childStory == StoryKind::Header || childStory == StoryKind::Footer) return parent == NodeKind::Document;
        return IsControlKind(parent) || parent == NodeKind::TableCell;
    }
    if (child == NodeKind::Section) return parent == NodeKind::Story && parentStory == StoryKind::Body;
    if (child == NodeKind::Paragraph)
        return parent == NodeKind::Section || (parent == NodeKind::Story && parentStory != StoryKind::Body);
    if (child == NodeKind::TableCell) return parent == NodeKind::Table;
    if (child == NodeKind::CharacterRun || child == NodeKind::SpecialCharacter ||
        child == NodeKind::GeneratedText || IsControlKind(child)) return parent == NodeKind::Paragraph;
    return false;
}
constexpr bool IsDefinition(const NodeKind kind, const DefinitionKind actual, const DefinitionKind wanted) noexcept {
    return kind == NodeKind::Definition && actual == wanted;
}
constexpr bool IsLegalReferenceEdge(
    const EdgeKind edge, const NodeKind source, const NodeKind target,
    const DefinitionKind sourceDefinition = DefinitionKind::Style,
    const DefinitionKind targetDefinition = DefinitionKind::Style,
    const StoryKind sourceStory = StoryKind::Other,
    const StoryKind targetStory = StoryKind::Other) noexcept {
    switch (edge) {
    case EdgeKind::Contains: return IsLegalPrimaryParent(target, source, targetStory, sourceStory);
    case EdgeKind::Anchors: return IsControlKind(source) && target == NodeKind::Paragraph;
    case EdgeKind::StyleRef: return source == NodeKind::Paragraph && IsDefinition(target,targetDefinition,DefinitionKind::Style);
    case EdgeKind::CharacterShapeRef:
        return (source == NodeKind::CharacterRun || IsDefinition(source,sourceDefinition,DefinitionKind::Style)) &&
            IsDefinition(target,targetDefinition,DefinitionKind::CharacterShape);
    case EdgeKind::ParagraphShapeRef:
        return (source == NodeKind::Paragraph || IsDefinition(source,sourceDefinition,DefinitionKind::Style)) &&
            IsDefinition(target,targetDefinition,DefinitionKind::ParagraphShape);
    case EdgeKind::NumberingRef:
        return (source == NodeKind::Paragraph || IsDefinition(source,sourceDefinition,DefinitionKind::Style) ||
                IsDefinition(source,sourceDefinition,DefinitionKind::ParagraphShape)) &&
            IsDefinition(target,targetDefinition,DefinitionKind::Numbering);
    case EdgeKind::BorderFillRef:
        return (source == NodeKind::TableCell || source == NodeKind::GenericControl || source == NodeKind::Definition) &&
            IsDefinition(target,targetDefinition,DefinitionKind::BorderFill);
    case EdgeKind::TabDefRef:
        return (source == NodeKind::Paragraph || IsDefinition(source,sourceDefinition,DefinitionKind::Style) ||
                IsDefinition(source,sourceDefinition,DefinitionKind::ParagraphShape)) &&
            IsDefinition(target,targetDefinition,DefinitionKind::TabDef);
    case EdgeKind::PageDefRef: return source == NodeKind::Section && IsDefinition(target,targetDefinition,DefinitionKind::PageDef);
    case EdgeKind::ColumnDefRef: return source == NodeKind::Section && IsDefinition(target,targetDefinition,DefinitionKind::ColumnDef);
    case EdgeKind::AssetRef: return (source == NodeKind::Image || source == NodeKind::GenericControl) && target == NodeKind::BinaryData;
    case EdgeKind::HeaderFooterApplies:
        return source == NodeKind::Section && target == NodeKind::Story &&
            (targetStory == StoryKind::Header || targetStory == StoryKind::Footer);
    case EdgeKind::CaptionOf: return source == NodeKind::Story && sourceStory == StoryKind::Caption && IsControlKind(target);
    case EdgeKind::OwnerStory:
        return source == NodeKind::Story && (sourceStory == StoryKind::TableCell || sourceStory == StoryKind::TextBox ||
            sourceStory == StoryKind::Footnote || sourceStory == StoryKind::Endnote || sourceStory == StoryKind::Caption) &&
            (target == NodeKind::TableCell || IsControlKind(target));
    }
    return false;
}

enum class EdgeCardinality : std::uint8_t { Zero = 0, ExactlyOne = 1, ZeroOrOne = 2, ZeroOrMany = 3 };
struct EdgeCardinalityRule final { EdgeKind edge; EdgeCardinality cardinality; };
inline constexpr std::array<EdgeCardinalityRule, 14> kEdgeCardinalityRules{{
    {EdgeKind::Contains,EdgeCardinality::ZeroOrMany},{EdgeKind::Anchors,EdgeCardinality::ExactlyOne},
    {EdgeKind::StyleRef,EdgeCardinality::ZeroOrOne},{EdgeKind::NumberingRef,EdgeCardinality::ZeroOrOne},
    {EdgeKind::BorderFillRef,EdgeCardinality::ZeroOrOne},{EdgeKind::TabDefRef,EdgeCardinality::ZeroOrOne},
    {EdgeKind::AssetRef,EdgeCardinality::ZeroOrOne},{EdgeKind::HeaderFooterApplies,EdgeCardinality::ZeroOrMany},
    {EdgeKind::CaptionOf,EdgeCardinality::ExactlyOne},{EdgeKind::OwnerStory,EdgeCardinality::ExactlyOne},
    {EdgeKind::CharacterShapeRef,EdgeCardinality::ExactlyOne},{EdgeKind::ParagraphShapeRef,EdgeCardinality::ExactlyOne},
    {EdgeKind::PageDefRef,EdgeCardinality::ExactlyOne},{EdgeKind::ColumnDefRef,EdgeCardinality::ExactlyOne},
}};
constexpr EdgeCardinality OutgoingCardinality(
    const EdgeKind edge, const NodeKind source, const bool coverageComplete,
    const DefinitionKind sourceDefinition = DefinitionKind::Style) noexcept {
    if (edge == EdgeKind::Contains || edge == EdgeKind::HeaderFooterApplies) return EdgeCardinality::ZeroOrMany;
    if (edge == EdgeKind::Anchors)
        return IsControlKind(source) ? EdgeCardinality::ExactlyOne : EdgeCardinality::Zero;
    if (edge == EdgeKind::CaptionOf || edge == EdgeKind::OwnerStory) return EdgeCardinality::ExactlyOne;
    if (edge == EdgeKind::CharacterShapeRef) {
        return source == NodeKind::CharacterRun && coverageComplete ? EdgeCardinality::ExactlyOne : EdgeCardinality::ZeroOrOne;
    }
    if (edge == EdgeKind::ParagraphShapeRef) {
        return source == NodeKind::Paragraph && coverageComplete ? EdgeCardinality::ExactlyOne : EdgeCardinality::ZeroOrOne;
    }
    if (edge == EdgeKind::PageDefRef || edge == EdgeKind::ColumnDefRef) {
        return coverageComplete ? EdgeCardinality::ExactlyOne : EdgeCardinality::ZeroOrOne;
    }
    static_cast<void>(sourceDefinition);
    return EdgeCardinality::ZeroOrOne;
}
constexpr EdgeCardinality IncomingCardinality(
    const EdgeKind edge, const NodeKind target) noexcept {
    if (edge == EdgeKind::Contains) return target == NodeKind::Document ? EdgeCardinality::Zero : EdgeCardinality::ExactlyOne;
    if (edge == EdgeKind::CaptionOf && IsControlKind(target)) return EdgeCardinality::ZeroOrOne;
    return EdgeCardinality::ZeroOrMany;
}
constexpr ProfileId OwningProfile(const DefinitionKind kind) noexcept {
    if (kind == DefinitionKind::BorderFill) return ProfileId::EditableObjects;
    if (kind == DefinitionKind::PageDef || kind == DefinitionKind::ColumnDef) return ProfileId::Layout;
    return ProfileId::EditableText;
}
constexpr ProfileId ResolveNodeFieldProfile(
    const NodeFieldRule& rule, const DefinitionKind definitionKind) noexcept {
    return rule.profileBinding == FieldProfileBinding::DefinitionOwning
        ? OwningProfile(definitionKind)
        : rule.profile;
}
constexpr bool IsCoverageStateLegal(const StreamKind stream, const CoverageState state) noexcept {
    return state != CoverageState::ProjectionOmitted || stream == StreamKind::QueryView;
}

struct FieldHeaderView final { FieldTag tag; std::uint16_t flags; std::uint64_t valueBytes; };
enum class SchemaError : std::uint8_t {
    None = 0, DuplicateField = 1, OutOfOrderField = 2, UnknownRequiredField = 3,
    ReservedField = 4, ProjectionOmittedInCapture = 5, DuplicatePropertyKey = 6,
    PropertyKeyOutOfOrder = 7, IllegalParent = 8, IllegalEdge = 9,
    WidthOverflow = 10, NonFiniteFloat = 11, NegativePublicCoordinate = 12,
};
template <std::size_t N>
constexpr bool IsKnownField(const std::array<FieldTag, N>& known, const FieldTag tag) noexcept {
    for (const FieldTag item : known) if (item == tag) return true;
    return false;
}
template <std::size_t N>
constexpr SchemaError ValidateFieldHeaders(
    const FieldHeaderView* fields, const std::size_t count,
    const std::array<FieldTag, N>& known) noexcept {
    FieldTag previous = 0;
    for (std::size_t index = 0; index < count; ++index) {
        const FieldHeaderView field = fields[index];
        if (field.tag == kReservedCommonFieldTagV1) return SchemaError::ReservedField;
        if (index != 0 && field.tag == previous) return SchemaError::DuplicateField;
        if (index != 0 && field.tag < previous) return SchemaError::OutOfOrderField;
        if (!IsKnownField(known, field.tag) && (field.flags & kFieldFlagRequired) != 0) {
            return SchemaError::UnknownRequiredField;
        }
        previous = field.tag;
    }
    return SchemaError::None;
}
constexpr SchemaError ValidateSortedPropertyKeys(
    const PropertyKeyId* keys, const std::size_t count) noexcept {
    for (std::size_t index = 1; index < count; ++index) {
        if (keys[index] == keys[index - 1]) return SchemaError::DuplicatePropertyKey;
        if (keys[index] < keys[index - 1]) return SchemaError::PropertyKeyOutOfOrder;
    }
    return SchemaError::None;
}

enum class CaptureBlockPhase : std::uint8_t {
    Manifest = 0, Document = 1, Definitions = 2, BinaryData = 3,
    RemainingPrimaryNodes = 4, Tombstones = 5, Remaps = 6,
};
enum class NodeBlockPhase : std::uint8_t {
    Node = 0, Properties = 1, Coverage = 2, Diagnostics = 3, Edges = 4,
    AssetChunks = 5,
};
enum class CanonicalOrderKey : std::uint8_t {
    NativeParagraphThenOffset = 0, EqualOffsetAtomPriority = 1,
    CellRowColumnAddress = 2, DefinitionKindNativeIdPropertyDigest = 3,
    BinaryDigestThenLength = 4, EdgeKindThenCanonicalTarget = 5,
};
enum class DeduplicationKey : std::uint8_t {
    DefinitionKindNativeIdSemanticPropertyAggregate = 0,
    BinaryLengthAndSha256 = 1,
};

static_assert(static_cast<std::uint8_t>(ObservationState::ProjectionOmitted) == 6);
static_assert(static_cast<std::uint8_t>(CoverageState::ProjectionOmitted) == 5);
static_assert(static_cast<std::uint8_t>(StreamKind::QueryView) == 1);
static_assert(static_cast<std::uint16_t>(ScalarTag::Sint32) == 17);

} // namespace hancom::graph
