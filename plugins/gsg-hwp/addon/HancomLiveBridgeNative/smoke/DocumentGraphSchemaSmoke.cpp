#include "../DocumentGraphProperties.h"
#include "../DocumentGraphSchema.h"
#include "../DocumentGraphTypes.h"

#include <array>
#include <cstdint>
#include <cwchar>
#include <iostream>
#include <limits>
#include <string_view>

namespace {
using namespace hancom::graph;

bool Expect(const bool condition, const wchar_t* const label) {
    if (!condition) std::wcerr << L"DocumentGraphSchemaSmoke failed: " << label << L'\n';
    return condition;
}
template <typename Enum, std::size_t N>
bool DenseTags(const std::array<Enum, N>& values) {
    for (std::size_t index = 0; index < N; ++index) {
        if (static_cast<std::uint64_t>(values[index]) != index) return false;
    }
    return true;
}
void HashByte(std::uint64_t* const hash, const std::uint8_t value) {
    *hash = (*hash ^ value) * UINT64_C(1099511628211);
}
void HashValue(std::uint64_t* const hash, std::uint64_t value, const unsigned bytes) {
    for (unsigned index = 0; index < bytes; ++index) {
        HashByte(hash, static_cast<std::uint8_t>(value & 0xffU));
        value >>= 8;
    }
}
std::uint64_t PropertyRegistryDigest() {
    std::uint64_t hash = UINT64_C(14695981039346656037);
    for (const PropertyRule& rule : kPropertyRegistryV1) {
        HashValue(&hash,rule.id,4); HashValue(&hash,static_cast<std::uint8_t>(rule.domain),1);
        HashValue(&hash,static_cast<std::uint16_t>(rule.scalar),2);
        HashValue(&hash,static_cast<std::uint8_t>(rule.valueShape),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.applicabilitySet),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.origin),1);
        HashValue(&hash,rule.profileBits,8); HashValue(&hash,static_cast<std::uint8_t>(rule.root),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.writability),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.requirement),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.qualifiers),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.sourceContract),1);
        for (const wchar_t* character = rule.nativeMemberPath; *character != L'\0'; ++character) {
            HashValue(&hash,static_cast<std::uint16_t>(*character),2);
        }
        HashValue(&hash,0,2);
        for (const wchar_t* path : {rule.nativeLowMemberPath,rule.nativeHighMemberPath}) {
            if (path!=nullptr) {
                for (const wchar_t* character=path; *character!=L'\0'; ++character)
                    HashValue(&hash,static_cast<std::uint16_t>(*character),2);
            }
            HashValue(&hash,0,2);
        }
    }
    return hash;
}

std::uint64_t PropertyAliasDigest() {
    std::uint64_t hash=UINT64_C(14695981039346656037);
    for (const PropertyAliasRule& alias:kPropertyAliasesV1) {
        HashValue(&hash,alias.canonicalId,4);
        for (const wchar_t* character=alias.aliasMemberPath; *character!=L'\0'; ++character)
            HashValue(&hash,static_cast<std::uint16_t>(*character),2);
        HashValue(&hash,0,2);
    }
    return hash;
}

std::uint64_t NodeFieldTableDigest() {
    std::uint64_t hash=UINT64_C(14695981039346656037);
    const auto append=[&hash](const NodeFieldRule& rule) {
        HashValue(&hash,static_cast<std::uint16_t>(rule.node),2); HashValue(&hash,rule.tag,2);
        HashValue(&hash,static_cast<std::uint16_t>(rule.scalar),2);
        HashValue(&hash,static_cast<std::uint8_t>(rule.requirement),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.profile),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.profileBinding),1);
        HashValue(&hash,static_cast<std::uint8_t>(rule.root),1);
        HashValue(&hash,rule.observation ? 1U : 0U,1); HashValue(&hash,rule.array ? 1U : 0U,1);
    };
    for (const NodeFieldRule& rule:kCommonNodeFieldRules) append(rule);
    for (const NodeFieldRule& rule:kNodePayloadFieldRules) append(rule);
    return hash;
}
std::uint64_t RecordFieldTableDigest() {
    std::uint64_t hash=UINT64_C(14695981039346656037);
    for (const RecordFieldRule& rule:kRecordFieldRules) {
        HashValue(&hash,static_cast<std::uint16_t>(rule.record),2); HashValue(&hash,rule.tag,2);
        HashValue(&hash,static_cast<std::uint16_t>(rule.scalar),2);
        HashValue(&hash,static_cast<std::uint8_t>(rule.requirement),1);
    }
    return hash;
}
std::uint64_t LocatorFieldTableDigest() {
    std::uint64_t hash=UINT64_C(14695981039346656037);
    for (const LocatorFieldRule& rule:kLocatorFieldRules) {
        HashValue(&hash,static_cast<std::uint16_t>(rule.locator),2); HashValue(&hash,rule.tag,2);
        HashValue(&hash,static_cast<std::uint16_t>(rule.scalar),2);
    }
    return hash;
}

bool NumericTagsSmoke() {
    return
        DenseTags(std::array{CaptureIntegrity::Complete,CaptureIntegrity::UnknownOmission,
            CaptureIntegrity::Cancelled,CaptureIntegrity::RouteChanged,CaptureIntegrity::StorageFailure,
            CaptureIntegrity::TraversalFailure,CaptureIntegrity::StateRestoreFailure}) &&
        DenseTags(std::array{ObservationState::Value,ObservationState::NotApplicable,
            ObservationState::NotExposed,ObservationState::ReadFailed,
            ObservationState::InconsistentNativeState,ObservationState::NotRequested,
            ObservationState::ProjectionOmitted}) &&
        DenseTags(std::array{CoverageState::Complete,CoverageState::NotRequested,
            CoverageState::NotApplicable,CoverageState::NotExposed,CoverageState::ReadFailed,
            CoverageState::ProjectionOmitted}) &&
        DenseTags(std::array{Writability::ReadOnly,Writability::Writable}) &&
        DenseTags(std::array{StreamKind::Capture,StreamKind::QueryView}) &&
        DenseTags(std::array{RecordKind::Manifest,RecordKind::Node,RecordKind::Edge,
            RecordKind::Property,RecordKind::Coverage,RecordKind::Diagnostic,
            RecordKind::AssetChunk,RecordKind::Tombstone,RecordKind::Remap}) &&
        DenseTags(std::array{ProfileId::Structure,ProfileId::EditableText,
            ProfileId::EditableObjects,ProfileId::Layout,ProfileId::BinaryContent}) &&
        DenseTags(std::array{NodeKind::Document,NodeKind::Story,NodeKind::Section,
            NodeKind::Paragraph,NodeKind::CharacterRun,NodeKind::SpecialCharacter,
            NodeKind::GeneratedText,NodeKind::GenericControl,NodeKind::Table,
            NodeKind::TableCell,NodeKind::Image,NodeKind::BinaryData,NodeKind::Definition}) &&
        DenseTags(std::array{DefinitionKind::Style,DefinitionKind::CharacterShape,
            DefinitionKind::ParagraphShape,DefinitionKind::Numbering,DefinitionKind::BorderFill,
            DefinitionKind::TabDef,DefinitionKind::PageDef,DefinitionKind::ColumnDef}) &&
        DenseTags(std::array{StoryKind::Body,StoryKind::Header,StoryKind::Footer,
            StoryKind::Footnote,StoryKind::Endnote,StoryKind::Caption,StoryKind::TableCell,
            StoryKind::TextBox,StoryKind::Other}) &&
        DenseTags(std::array{EdgeKind::Contains,EdgeKind::Anchors,EdgeKind::StyleRef,
            EdgeKind::NumberingRef,EdgeKind::BorderFillRef,EdgeKind::TabDefRef,
            EdgeKind::AssetRef,EdgeKind::HeaderFooterApplies,EdgeKind::CaptionOf,
            EdgeKind::OwnerStory,EdgeKind::CharacterShapeRef,EdgeKind::ParagraphShapeRef,
            EdgeKind::PageDefRef,EdgeKind::ColumnDefRef}) &&
        DenseTags(std::array{PropertyDomain::CharacterShape,PropertyDomain::ParagraphShape,
            PropertyDomain::Style,PropertyDomain::Numbering,PropertyDomain::BorderFill,
            PropertyDomain::TabDef,PropertyDomain::PageSetup,PropertyDomain::Section,
            PropertyDomain::Control,PropertyDomain::Table,PropertyDomain::Cell,
            PropertyDomain::Image,PropertyDomain::Layout,PropertyDomain::NativeExtension,
            PropertyDomain::DocumentMetadata}) &&
        DenseTags(std::array{ScalarTag::Sint64,ScalarTag::Uint64,ScalarTag::Bool,
            ScalarTag::Float64,ScalarTag::UTF16,ScalarTag::HWPUNIT64,ScalarTag::BGR,
            ScalarTag::Enum,ScalarTag::BlobSlice,ScalarTag::UUID128,ScalarTag::SHA256,
            ScalarTag::Struct,ScalarTag::RawURC32,ScalarTag::Bytes,ScalarTag::Uint8,
            ScalarTag::Uint16,ScalarTag::Uint32,ScalarTag::Sint32}) &&
        DenseTags(std::array{LocatorTag::None,LocatorTag::Story,LocatorTag::Paragraph,
            LocatorTag::Run,LocatorTag::Control,LocatorTag::Cell}) &&
        DenseTags(std::array{PropertyOrigin::Direct,PropertyOrigin::Inherited,
            PropertyOrigin::Generated,PropertyOrigin::Unknown,
            PropertyOrigin::UserOverride,PropertyOrigin::LocalStyle,
            PropertyOrigin::NamedStyle,PropertyOrigin::DocumentDefault,
            PropertyOrigin::ImplicitDefault,PropertyOrigin::NotApplicable,
            PropertyOrigin::Unavailable});
}

bool FieldTablesSmoke() {
    const std::uint64_t nodeFieldDigest=NodeFieldTableDigest();
    // Todo17 adds skippable Caption Story observations for numbering, style,
    // and page span; the record and locator schemas remain frozen.
    if (nodeFieldDigest!=UINT64_C(0x2abb7fbc8000b324) ||
        RecordFieldTableDigest()!=UINT64_C(0x688238a24da4adb4) ||
        LocatorFieldTableDigest()!=UINT64_C(0xf6f1c7d5561f1f6c)) {
        std::wcerr << L"node field digest=0x" << std::hex
                   << nodeFieldDigest << std::dec << L'\n';
        return false;
    }
    constexpr std::array<std::size_t,9> recordCounts{6,2,4,5,7,6,5,3,4};
    std::array<std::size_t,9> actual{};
    for (const RecordFieldRule& rule : kRecordFieldRules) {
        const auto kind=static_cast<std::size_t>(rule.record);
        if (kind>=actual.size() || rule.tag==0 || rule.tag>7) return false;
        ++actual[kind];
    }
    if (actual != recordCounts) return false;
    constexpr std::array<NodeKind,60> owners{
        NodeKind::Document,
        NodeKind::Story,NodeKind::Story,NodeKind::Story,
        NodeKind::Story,NodeKind::Story,NodeKind::Story,
        NodeKind::Section,NodeKind::Section,NodeKind::Section,NodeKind::Section,
        NodeKind::Paragraph,NodeKind::Paragraph,NodeKind::Paragraph,NodeKind::Paragraph,NodeKind::Paragraph,
        NodeKind::CharacterRun,NodeKind::CharacterRun,NodeKind::CharacterRun,
        NodeKind::SpecialCharacter,NodeKind::SpecialCharacter,NodeKind::SpecialCharacter,NodeKind::SpecialCharacter,
        NodeKind::GeneratedText,NodeKind::GeneratedText,NodeKind::GeneratedText,
        NodeKind::GenericControl,NodeKind::GenericControl,NodeKind::GenericControl,NodeKind::GenericControl,
        NodeKind::GenericControl,NodeKind::GenericControl,NodeKind::GenericControl,
        NodeKind::Table,NodeKind::Table,NodeKind::Table,
        NodeKind::TableCell,NodeKind::TableCell,NodeKind::TableCell,NodeKind::TableCell,NodeKind::TableCell,
        NodeKind::TableCell,NodeKind::TableCell,NodeKind::TableCell,NodeKind::TableCell,NodeKind::TableCell,
        NodeKind::Image,NodeKind::Image,NodeKind::Image,NodeKind::Image,NodeKind::Image,NodeKind::Image,
        NodeKind::BinaryData,NodeKind::BinaryData,NodeKind::BinaryData,NodeKind::BinaryData,
        NodeKind::Definition,NodeKind::Definition,NodeKind::Definition,NodeKind::Definition};
    constexpr std::array<FieldTag,60> tags{
        100,100,101,102,103,104,105,
        100,101,102,103,100,101,102,103,104,100,101,102,
        100,101,102,103,100,101,102,100,101,102,103,104,105,106,107,108,109,
        100,101,102,103,104,105,106,107,108,109,107,108,109,110,111,112,
        100,101,102,103,100,101,102,103};
    for (std::size_t index=0; index<owners.size(); ++index) {
        const bool captionExtension =
            owners[index] == NodeKind::Story && tags[index] >= 103;
        if (kNodePayloadFieldRules[index].node!=owners[index] ||
            kNodePayloadFieldRules[index].tag!=tags[index] ||
            kNodePayloadFieldRules[index].requirement !=
                (captionExtension ? Requirement::Optional
                                  : Requirement::Required)) return false;
    }
    constexpr std::array<FieldTag,10> commonTags{1,2,3,4,5,6,7,8,10,11};
    for (std::size_t index=0; index<commonTags.size(); ++index) {
        if (kCommonNodeFieldRules[index].tag!=commonTags[index]) return false;
        if ((commonTags[index]==3) !=
            (kCommonNodeFieldRules[index].requirement==Requirement::Optional)) return false;
    }
    constexpr std::array<DefinitionKind,8> definitionKinds{
        DefinitionKind::Style,DefinitionKind::CharacterShape,DefinitionKind::ParagraphShape,
        DefinitionKind::Numbering,DefinitionKind::BorderFill,DefinitionKind::TabDef,
        DefinitionKind::PageDef,DefinitionKind::ColumnDef};
    constexpr std::array<ProfileId,8> definitionProfiles{
        ProfileId::EditableText,ProfileId::EditableText,ProfileId::EditableText,
        ProfileId::EditableText,ProfileId::EditableObjects,ProfileId::EditableText,
        ProfileId::Layout,ProfileId::Layout};
    for (const NodeFieldRule& rule:kCommonNodeFieldRules) {
        if (rule.profileBinding!=FieldProfileBinding::Fixed) return false;
    }
    for (const NodeFieldRule& rule:kNodePayloadFieldRules) {
        const bool definitionField=rule.node==NodeKind::Definition;
        if (definitionField!=(rule.profileBinding==FieldProfileBinding::DefinitionOwning)) return false;
        for (std::size_t index=0; index<definitionKinds.size(); ++index) {
            const ProfileId expected=definitionField ? definitionProfiles[index] : rule.profile;
            if (ResolveNodeFieldProfile(rule,definitionKinds[index])!=expected) return false;
        }
    }
    constexpr std::array<std::size_t,6> locatorCounts{0,1,2,4,4,3};
    std::array<std::size_t,6> found{};
    for (const LocatorFieldRule& rule:kLocatorFieldRules) ++found[static_cast<std::size_t>(rule.locator)];
    const auto requiredParagraphBag=[](const FieldTag tag) {
        const auto rule=std::find_if(
            kNodePayloadFieldRules.begin(),kNodePayloadFieldRules.end(),
            [tag](const NodeFieldRule& candidate) {
                return candidate.node==NodeKind::Paragraph &&
                    candidate.tag==tag;
            });
        return rule!=kNodePayloadFieldRules.end() &&
            rule->scalar==ScalarTag::Uint64 &&
            rule->requirement==Requirement::Required &&
            !rule->observation && rule->array;
    };
    return found==locatorCounts &&
        requiredParagraphBag(103) && requiredParagraphBag(104);
}

bool GrammarAndValueSmoke() {
    constexpr std::array<FieldTag,2> known{1,2};
    constexpr FieldHeaderView valid[]{{1,kFieldFlagRequired,8},{9,0,100}};
    constexpr FieldHeaderView duplicate[]{{1,kFieldFlagRequired,8},{1,0,8}};
    constexpr FieldHeaderView reserved[]{{9,0,0}};
    constexpr FieldHeaderView unknownRequired[]{{1,kFieldFlagRequired,8},{8,kFieldFlagRequired,99}};
    constexpr FieldHeaderView unknownOptional[]{{1,kFieldFlagRequired,8},{8,0,99}};
    constexpr PropertyKeyId duplicateKeys[]{1000,1000};
    constexpr PropertyKeyId reversedKeys[]{1001,1000};
    constexpr PropertyKeyId sortedKeys[]{1000,1001};
    constexpr std::uint16_t paired[]{0xd83d,0xde00};
    constexpr std::uint16_t unpaired[]{0xd800,0x0041,0xdc00};
    ObservationV1<std::uint64_t> value{};
    value.state=ObservationState::Value; value.valuePresent=true; value.hresult=0;
    ObservationV1<std::uint64_t> omitted{};
    omitted.state=ObservationState::ProjectionOmitted;
    constexpr std::uint64_t above4GiB=UINT64_C(0x100000001);
    Utf16SpoolSlice text{}; text.codeUnitCount=above4GiB; text.bytes.length=above4GiB*2U;
    return sizeof(LogicalRecordHeaderV1)==24 && sizeof(FieldHeaderV1)==24 &&
        sizeof(ArrayHeaderV1)==16 && kObservationEnvelopeFixedBytesV1==24 &&
        ValidateFieldHeaders(valid,1,known)==SchemaError::None &&
        ValidateFieldHeaders(duplicate,2,known)==SchemaError::DuplicateField &&
        ValidateFieldHeaders(reserved,1,known)==SchemaError::ReservedField &&
        ValidateFieldHeaders(unknownRequired,2,known)==SchemaError::UnknownRequiredField &&
        ValidateFieldHeaders(unknownOptional,2,known)==SchemaError::None &&
        ValidateSortedPropertyKeys(duplicateKeys,2)==SchemaError::DuplicatePropertyKey &&
        ValidateSortedPropertyKeys(reversedKeys,2)==SchemaError::PropertyKeyOutOfOrder &&
        ValidateSortedPropertyKeys(sortedKeys,2)==SchemaError::None &&
        IsValidObservation(StreamKind::Capture,value) &&
        !IsValidObservation(StreamKind::Capture,omitted) &&
        IsValidObservation(StreamKind::QueryView,omitted) &&
        !IsCoverageStateLegal(StreamKind::Capture,CoverageState::ProjectionOmitted) &&
        IsCoverageStateLegal(StreamKind::QueryView,CoverageState::ProjectionOmitted) &&
        IsValidRawUtf16({paired,2}) && IsValidRawUtf16({unpaired,3}) &&
        IsValidUtf16Slice(text) && above4GiB>UINT32_MAX &&
        !FitsUnsignedPersistedWidth(above4GiB,4) && FitsUnsignedPersistedWidth(above4GiB,8) &&
        IsValid(HalfOpenRange{5,5}) && IsValid(HalfOpenRange{5,8}) && !IsValid(HalfOpenRange{8,5}) &&
        IsValid(CellCoordinateV1{1,1,1,1}) && !IsValid(CellCoordinateV1{0,1,1,1}) &&
        IsValidPublicCoordinate(0) && !IsValidPublicCoordinate(-1) &&
        IsFiniteFloat64(1.25) && IsFiniteFloat64(-0.0) &&
        !IsFiniteFloat64((std::numeric_limits<double>::quiet_NaN)()) &&
        !IsFiniteFloat64((std::numeric_limits<double>::infinity)());
}

bool ProfilesParentsAndEdgesSmoke() {
    const std::uint64_t objectOnly=ProfileBit(ProfileId::EditableObjects);
    if (CloseProfileBits(objectOnly)!=(objectOnly|ProfileBit(ProfileId::EditableText)|ProfileBit(ProfileId::Structure)) ||
        CloseProfileBits(ProfileBit(ProfileId::Layout))!=(ProfileBit(ProfileId::Layout)|ProfileBit(ProfileId::Structure)) ||
        HasOnlyKnownProfiles(UINT64_C(1)<<63)) return false;
    if (kProfileRules.size()!=5 || kCoverageCoordinateRules.size()!=5 ||
        kCoverageCoordinateRules[0].profile!=kNoProfile || kCoverageCoordinateRules[4].propertyKeyPresent==false) return false;
    if (OutgoingCardinality(EdgeKind::Contains,NodeKind::Document,true)!=EdgeCardinality::ZeroOrMany ||
        IncomingCardinality(EdgeKind::Contains,NodeKind::Document)!=EdgeCardinality::Zero ||
        IncomingCardinality(EdgeKind::Contains,NodeKind::Paragraph)!=EdgeCardinality::ExactlyOne ||
        OutgoingCardinality(EdgeKind::CharacterShapeRef,NodeKind::CharacterRun,true)!=EdgeCardinality::ExactlyOne ||
        OutgoingCardinality(EdgeKind::CharacterShapeRef,NodeKind::Definition,true)!=EdgeCardinality::ZeroOrOne ||
        OwningProfile(DefinitionKind::BorderFill)!=ProfileId::EditableObjects ||
        OwningProfile(DefinitionKind::Style)!=ProfileId::EditableText) return false;
    if (IsLegalPrimaryParent(NodeKind::Document,NodeKind::Document) ||
        !IsLegalPrimaryParent(NodeKind::Definition,NodeKind::Document) ||
        !IsLegalPrimaryParent(NodeKind::BinaryData,NodeKind::Document) ||
        !IsLegalPrimaryParent(NodeKind::Section,NodeKind::Story,StoryKind::Other,StoryKind::Body) ||
        IsLegalPrimaryParent(NodeKind::Section,NodeKind::Document) ||
        !IsLegalPrimaryParent(NodeKind::Paragraph,NodeKind::Section) ||
        IsLegalPrimaryParent(NodeKind::Paragraph,NodeKind::Story,StoryKind::Other,StoryKind::Body) ||
        !IsLegalPrimaryParent(NodeKind::Paragraph,NodeKind::Story,StoryKind::Other,StoryKind::Header) ||
        !IsLegalPrimaryParent(NodeKind::CharacterRun,NodeKind::Paragraph) ||
        !IsLegalPrimaryParent(NodeKind::GenericControl,NodeKind::Paragraph) ||
        !IsLegalPrimaryParent(NodeKind::TableCell,NodeKind::Table) ||
        !IsLegalPrimaryParent(NodeKind::Story,NodeKind::TableCell,StoryKind::TableCell)) return false;
    return
        IsLegalReferenceEdge(EdgeKind::Contains,NodeKind::Document,NodeKind::Definition) &&
        IsLegalReferenceEdge(EdgeKind::Contains,NodeKind::Section,NodeKind::Paragraph) &&
        !IsLegalReferenceEdge(EdgeKind::Contains,NodeKind::Story,NodeKind::Paragraph,
            DefinitionKind::Style,DefinitionKind::Style,StoryKind::Body,StoryKind::Other) &&
        IsLegalReferenceEdge(EdgeKind::Contains,NodeKind::Story,NodeKind::Paragraph,
            DefinitionKind::Style,DefinitionKind::Style,StoryKind::Header,StoryKind::Other) &&
        IsLegalReferenceEdge(EdgeKind::Anchors,NodeKind::Table,NodeKind::Paragraph) &&
        IsLegalReferenceEdge(EdgeKind::StyleRef,NodeKind::Paragraph,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::Style) &&
        IsLegalReferenceEdge(EdgeKind::NumberingRef,NodeKind::Paragraph,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::Numbering) &&
        IsLegalReferenceEdge(EdgeKind::BorderFillRef,NodeKind::TableCell,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::BorderFill) &&
        IsLegalReferenceEdge(EdgeKind::TabDefRef,NodeKind::Paragraph,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::TabDef) &&
        IsLegalReferenceEdge(EdgeKind::AssetRef,NodeKind::Image,NodeKind::BinaryData) &&
        IsLegalReferenceEdge(EdgeKind::HeaderFooterApplies,NodeKind::Section,NodeKind::Story,
            DefinitionKind::Style,DefinitionKind::Style,StoryKind::Other,StoryKind::Header) &&
        IsLegalReferenceEdge(EdgeKind::CaptionOf,NodeKind::Story,NodeKind::Image,
            DefinitionKind::Style,DefinitionKind::Style,StoryKind::Caption) &&
        IsLegalReferenceEdge(EdgeKind::OwnerStory,NodeKind::Story,NodeKind::TableCell,
            DefinitionKind::Style,DefinitionKind::Style,StoryKind::TableCell) &&
        IsLegalReferenceEdge(EdgeKind::CharacterShapeRef,NodeKind::CharacterRun,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::CharacterShape) &&
        IsLegalReferenceEdge(EdgeKind::ParagraphShapeRef,NodeKind::Paragraph,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::ParagraphShape) &&
        IsLegalReferenceEdge(EdgeKind::PageDefRef,NodeKind::Section,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::PageDef) &&
        IsLegalReferenceEdge(EdgeKind::ColumnDefRef,NodeKind::Section,NodeKind::Definition,
            DefinitionKind::Style,DefinitionKind::ColumnDef) &&
        !IsLegalReferenceEdge(EdgeKind::Anchors,NodeKind::Paragraph,NodeKind::Table) &&
        !IsLegalReferenceEdge(EdgeKind::AssetRef,NodeKind::Table,NodeKind::BinaryData);
}

struct ExpectedBagTuple final {
    NodeKind node;
    FieldTag ownerField;
    bool definitionRestricted;
    DefinitionKind definitionKind;
};
struct ExpectedPropertyBagGroup final {
    PropertyKeyId first;
    PropertyKeyId last;
    std::array<ExpectedBagTuple,5> tuples;
    std::size_t tupleCount;
};
inline constexpr std::array<ExpectedPropertyBagGroup,16> kExpectedPropertyBags{{
    {1000,1040,{{{NodeKind::CharacterRun,102,false,DefinitionKind::Style},
                 {NodeKind::Paragraph,104,false,DefinitionKind::Style},
                 {NodeKind::Definition,102,true,DefinitionKind::CharacterShape}}},3},
    {2000,2013,{{{NodeKind::Paragraph,103,false,DefinitionKind::Style},
                 {NodeKind::Definition,102,true,DefinitionKind::ParagraphShape}}},2},
    {3000,3004,{{{NodeKind::Definition,102,true,DefinitionKind::Style}}},1},
    {4000,4001,{{{NodeKind::Definition,102,true,DefinitionKind::Numbering}}},1},
    {5000,5015,{{{NodeKind::Definition,102,true,DefinitionKind::BorderFill}}},1},
    {6000,6002,{{{NodeKind::Definition,102,true,DefinitionKind::TabDef}}},1},
    {7000,7010,{{{NodeKind::Definition,102,true,DefinitionKind::PageDef}}},1},
    {7100,7104,{{{NodeKind::Definition,102,true,DefinitionKind::ColumnDef}}},1},
    {8000,8014,{{{NodeKind::GenericControl,106,false,DefinitionKind::Style},
                 {NodeKind::Table,106,false,DefinitionKind::Style},
                 {NodeKind::Image,106,false,DefinitionKind::Style}}},3},
    {9000,9004,{{{NodeKind::Table,106,false,DefinitionKind::Style}}},1},
    {10000,10025,{{{NodeKind::TableCell,109,false,DefinitionKind::Style}}},1},
    {11000,11009,{{{NodeKind::Image,112,false,DefinitionKind::Style}}},1},
    {11010,11010,{{{NodeKind::Image,11,false,DefinitionKind::Style}}},1},
    {12000,12001,{{{NodeKind::Paragraph,10,false,DefinitionKind::Style},
                   {NodeKind::GenericControl,10,false,DefinitionKind::Style},
                   {NodeKind::Table,10,false,DefinitionKind::Style},
                   {NodeKind::TableCell,10,false,DefinitionKind::Style},
                   {NodeKind::Image,10,false,DefinitionKind::Style}}},5},
    {12002,12003,{{{NodeKind::GenericControl,10,false,DefinitionKind::Style},
                   {NodeKind::Table,10,false,DefinitionKind::Style},
                   {NodeKind::Image,10,false,DefinitionKind::Style}}},3},
    {13000,13010,{{{NodeKind::Document,100,false,DefinitionKind::Style}}},1},
}};

const ExpectedPropertyBagGroup* ExpectedBagGroup(const PropertyKeyId id) {
    for (const ExpectedPropertyBagGroup& group:kExpectedPropertyBags) {
        if (id>=group.first && id<=group.last) return &group;
    }
    return nullptr;
}
bool ExpectedApplicable(
    const ExpectedPropertyBagGroup& group, const NodeKind node,
    const FieldTag field, const bool hasDefinitionKind,
    const DefinitionKind definitionKind) {
    for (std::size_t index=0; index<group.tupleCount; ++index) {
        const ExpectedBagTuple tuple=group.tuples[index];
        if (tuple.node!=node || tuple.ownerField!=field) continue;
        if (!tuple.definitionRestricted) return true;
        if (hasDefinitionKind && tuple.definitionKind==definitionKind) return true;
    }
    return false;
}
bool ExhaustivePropertyApplicabilitySmoke() {
    constexpr std::array<DefinitionKind,8> definitions{
        DefinitionKind::Style,DefinitionKind::CharacterShape,
        DefinitionKind::ParagraphShape,DefinitionKind::Numbering,
        DefinitionKind::BorderFill,DefinitionKind::TabDef,
        DefinitionKind::PageDef,DefinitionKind::ColumnDef};
    for (const PropertyRule& rule:kPropertyRegistryV1) {
        const ExpectedPropertyBagGroup* const group=ExpectedBagGroup(rule.id);
        if (group==nullptr) return false;
        for (std::uint16_t rawNode=0; rawNode<=static_cast<std::uint16_t>(NodeKind::Definition); ++rawNode) {
            const NodeKind node=static_cast<NodeKind>(rawNode);
            for (FieldTag field=0; field<=112; ++field) {
                const bool expectedWithoutKind=ExpectedApplicable(
                    *group,node,field,false,DefinitionKind::Style);
                if (IsPropertyApplicable(rule,node,field,false,DefinitionKind::Style)!=expectedWithoutKind) {
                    std::wcerr << L"applicability id=" << rule.id << L" node=" << rawNode
                               << L" field=" << field << L'\n';
                    return false;
                }
                if (node==NodeKind::Definition) {
                    for (const DefinitionKind definition:definitions) {
                        const bool expected=ExpectedApplicable(*group,node,field,true,definition);
                        if (IsPropertyApplicable(rule,node,field,true,definition)!=expected) return false;
                    }
                }
            }
        }
    }
    return true;
}

bool PropertyRegistryAmendmentSmoke() {
    if (kPropertyRegistryV1.size()!=165 || kWithdrawnPropertyKeyIdsV1.size()!=4 ||
        kWithdrawnPropertyKeyIdsV1!=std::array<PropertyKeyId,4>{1015,1016,1017,7103}) return false;
    std::size_t required=0, optional=0;
    for (const PropertyRule& rule:kPropertyRegistryV1) {
        if (rule.requirement==RequirementClass::RequiredWhenApplicable) ++required;
        else if (rule.requirement==RequirementClass::OptionalObserved) ++optional;
        else return false;
        for (const PropertyKeyId withdrawn:kWithdrawnPropertyKeyIdsV1) {
            if (rule.id==withdrawn || FindPropertyRule(withdrawn)!=nullptr) return false;
        }
    }
    if (required!=132 || optional!=33 || FindPropertyRule(1015)!=nullptr ||
        FindPropertyRule(1016)!=nullptr || FindPropertyRule(1017)!=nullptr ||
        FindPropertyRule(7103)!=nullptr) return false;

    constexpr std::array<const wchar_t*,7> scripts{L"Hangul",L"Latin",L"Hanja",L"Japanese",L"Other",L"Symbol",L"User"};
    for (std::size_t index=0; index<scripts.size(); ++index) {
        const PropertyRule* const spacing=FindPropertyRule(static_cast<PropertyKeyId>(1020+index));
        const PropertyRule* const ratio=FindPropertyRule(static_cast<PropertyKeyId>(1027+index));
        const PropertyRule* const offset=FindPropertyRule(static_cast<PropertyKeyId>(1034+index));
        if (spacing==nullptr || ratio==nullptr || offset==nullptr ||
            std::wstring_view(spacing->nativeMemberPath)!=std::wstring(L"HCharShape.Spacing")+scripts[index] ||
            std::wstring_view(ratio->nativeMemberPath)!=std::wstring(L"HCharShape.Ratio")+scripts[index] ||
            std::wstring_view(offset->nativeMemberPath)!=std::wstring(L"HCharShape.Offset")+scripts[index] ||
            spacing->scalar!=ScalarTag::Sint64 || ratio->scalar!=ScalarTag::Uint8 ||
            offset->scalar!=ScalarTag::Sint64 || spacing->requirement!=RequirementClass::OptionalObserved ||
            ratio->requirement!=RequirementClass::OptionalObserved || offset->requirement!=RequirementClass::OptionalObserved) return false;
    }
    const PropertyRule* const brush=FindPropertyRule(5015);
    const PropertyRule* const gap=FindPropertyRule(7104);
    if (brush==nullptr || std::wstring_view(brush->nativeMemberPath)!=L"HCellBorderFill.FillAttr.WindowsBrush" ||
        brush->scalar!=ScalarTag::Uint8 || brush->applicabilitySet!=PropertyApplicabilitySet::BorderFillDefinition ||
        brush->origin!=RegistryOrigin::Direct || brush->profileBits!=ProfileBit(ProfileId::EditableObjects) ||
        brush->root!=RootClass::Semantic || brush->requirement!=RequirementClass::RequiredWhenApplicable ||
        brush->qualifiers!=QualifierFlags::WindowsBrushFillCondition ||
        gap==nullptr || std::wstring_view(gap->nativeMemberPath)!=L"HColDef.WidthGap[]" ||
        gap->scalar!=ScalarTag::Uint16 || gap->valueShape!=PropertyValueShape::Uint16Array ||
        gap->requirement!=RequirementClass::OptionalObserved ||
        gap->qualifiers!=(QualifierFlags::VersionGated|QualifierFlags::OwnerQualificationRequired)) return false;

    struct TimeCombineCase final {
        std::uint32_t low;
        std::uint32_t high;
        std::uint64_t combined;
    };
    constexpr std::array<TimeCombineCase,3> timeCases{{
        {UINT32_C(0x89abcdef),UINT32_C(0x01234567),UINT64_C(0x0123456789abcdef)},
        {UINT32_C(0x10203040),UINT32_C(0xa1b2c3d4),UINT64_C(0xa1b2c3d410203040)},
        {UINT32_C(0xfedcba98),UINT32_C(0x76543210),UINT64_C(0x76543210fedcba98)},
    }};
    for (const TimeCombineCase test:timeCases) {
        if (CombinePropertyTimeLowHigh(test.low,test.high)!=test.combined) return false;
    }
    constexpr std::array<const wchar_t*,11> metadataNames{L"Title",L"Subject",L"Author",L"Date",L"KeyWords",L"Comments",L"CreationTime",L"ModifiedTime",L"PrintedTime",L"LastSavedBy",L"DocVersion"};
    constexpr std::array<const wchar_t*,3> timeLowPaths{
        L"SummaryInfo.CreationTimeLow",L"SummaryInfo.ModifiedTimeLow",L"SummaryInfo.PrintedTimeLow"};
    constexpr std::array<const wchar_t*,3> timeHighPaths{
        L"SummaryInfo.CreationTimeHigh",L"SummaryInfo.ModifiedTimeHigh",L"SummaryInfo.PrintedTimeHigh"};
    for (std::size_t index=0; index<metadataNames.size(); ++index) {
        const PropertyRule* const rule=FindPropertyRule(static_cast<PropertyKeyId>(13000+index));
        if (rule==nullptr || rule->domain!=PropertyDomain::DocumentMetadata ||
            rule->applicabilitySet!=PropertyApplicabilitySet::DocumentMetadata ||
            !IsPropertyApplicable(*rule,NodeKind::Document,100) ||
            rule->requirement!=RequirementClass::OptionalObserved ||
            std::wstring_view(rule->nativeMemberPath)!=std::wstring(L"SummaryInfo.")+metadataNames[index] ||
            rule->scalar!=((index>=6 && index<=8)?ScalarTag::Uint64:ScalarTag::UTF16) ||
            rule->root!=((index<=5)?RootClass::Semantic:RootClass::Capture) ||
            rule->sourceContract!=((index>=6 && index<=8)
                ?PropertySourceContract::LowHighUint32
                :PropertySourceContract::DirectValue) ||
            ((index>=6 && index<=8) &&
                (!WideEqual(rule->nativeLowMemberPath,timeLowPaths[index-6]) ||
                 !WideEqual(rule->nativeHighMemberPath,timeHighPaths[index-6]))) ||
            ((index<6 || index>8) &&
                (rule->nativeLowMemberPath!=nullptr || rule->nativeHighMemberPath!=nullptr))) return false;
    }
    const PropertyRule* const outline=FindPropertyRule(1018);
    const PropertyRule* const keep=FindPropertyRule(2009);
    const PropertyRule* const pagebreak=FindPropertyRule(2010);
    const PropertyRule* const color=FindPropertyRule(5002);
    return outline!=nullptr && keep!=nullptr && pagebreak!=nullptr && color!=nullptr &&
        std::wstring_view(outline->nativeMemberPath)==L"HCharShape.OutlineType" &&
        std::wstring_view(keep->nativeMemberPath)==L"HParaShape.KeepLinesTogether" &&
        std::wstring_view(pagebreak->nativeMemberPath)==L"HParaShape.PagebreakBefore" &&
        std::wstring_view(color->nativeMemberPath)==L"HCellBorderFill.BorderCorlorLeft" &&
        FindPropertyRuleByMemberPath(L"HCharShape.OutLineType")==outline &&
        FindPropertyRuleByMemberPath(L"HParaShape.KeepLines")==keep &&
        FindPropertyRuleByMemberPath(L"HParaShape.PageBreakBefore")==pagebreak &&
        FindPropertyRuleByMemberPath(L"HCellBorderFill.BorderColorLeft")==color &&
        MissingPropertySatisfiesCompleteness(*gap,true) &&
        ObservationSatisfiesCompleteness(*gap,ObservationState::NotExposed,true) &&
        !MissingPropertySatisfiesCompleteness(*brush,true) &&
        !ObservationSatisfiesCompleteness(*brush,ObservationState::NotExposed,true) &&
        !ObservationSatisfiesCompleteness(*brush,ObservationState::NotApplicable,true) &&
        ObservationSatisfiesCompleteness(*brush,ObservationState::NotApplicable,false);
}

bool PropertyOriginStateMatrixSmoke() {
    for (const PropertyOrigin origin : kEffectiveValueOriginsV1) {
        if (!PropertyOriginAllowedForObservation(
                RegistryOrigin::Effective, ObservationState::Value, origin)) {
            return false;
        }
    }
    return PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::NotApplicable,
               PropertyOrigin::NotApplicable) &&
        PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::NotExposed,
               PropertyOrigin::Unavailable) &&
        PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::ReadFailed,
               PropertyOrigin::Unavailable) &&
        !PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::Value,
               PropertyOrigin::NotApplicable) &&
        !PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::NotApplicable,
               PropertyOrigin::Unavailable) &&
        !PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::NotApplicable,
               PropertyOrigin::UserOverride) &&
        !PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::NotExposed,
               PropertyOrigin::NamedStyle) &&
        !PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::Value,
               PropertyOrigin::Generated) &&
        !PropertyOriginAllowedForObservation(
               RegistryOrigin::Effective,
               ObservationState::Value,
               static_cast<PropertyOrigin>(255));
}

bool PropertiesSmoke() {
    if (!ExhaustivePropertyApplicabilitySmoke()) return false;
    const std::uint64_t registryDigest=PropertyRegistryDigest();
    const std::uint64_t aliasDigest=PropertyAliasDigest();
    if (registryDigest!=UINT64_C(0x3c646f711ff6dd23) ||
        aliasDigest!=UINT64_C(0xc95980264a80b0e5)) {
        std::wcerr << L"property registry digest=0x" << std::hex << registryDigest
                   << L" alias=0x" << aliasDigest << std::dec << L'\n';
        return false;
    }
    for (const PropertyRule& rule:kPropertyRegistryV1) {
        if (FindPropertyRule(rule.id)!=&rule || rule.writability!=Writability::ReadOnly ||
            !HasOnlyKnownProfiles(rule.profileBits) || rule.profileBits==0) return false;
    }
    const PropertyRule* const character=FindPropertyRule(1000);
    const PropertyRule* const paragraph=FindPropertyRule(2000);
    const PropertyRule* const table=FindPropertyRule(9000);
    const PropertyRule* const cell=FindPropertyRule(10000);
    const PropertyRule* const image=FindPropertyRule(11000);
    const PropertyRule* const binary=FindPropertyRule(11010);
    const PropertyRule* const layout=FindPropertyRule(12000);
    return character!=nullptr && paragraph!=nullptr && table!=nullptr && cell!=nullptr &&
        image!=nullptr && binary!=nullptr && layout!=nullptr &&
        IsPropertyApplicable(*character,NodeKind::CharacterRun,102) &&
        IsPropertyApplicable(*character,NodeKind::Paragraph,104) &&
        IsPropertyApplicable(*character,NodeKind::Definition,102,true,DefinitionKind::CharacterShape) &&
        !IsPropertyApplicable(*character,NodeKind::Paragraph,103) &&
        IsPropertyApplicable(*paragraph,NodeKind::Paragraph,103) &&
        IsPropertyApplicable(*table,NodeKind::Table,106) &&
        !IsPropertyApplicable(*table,NodeKind::TableCell,109) &&
        IsPropertyApplicable(*cell,NodeKind::TableCell,109) &&
        IsPropertyApplicable(*image,NodeKind::Image,112) &&
        IsPropertyApplicable(*binary,NodeKind::Image,11) &&
        IsPropertyApplicable(*layout,NodeKind::Paragraph,10) &&
        FindPropertyRule(999)==nullptr && FindPropertyRule(12004)==nullptr;
}

} // namespace

bool DocumentGraphPropertyRegistrySmoke(const wchar_t* scenario) {
    if (scenario==nullptr) return false;
    if (std::wcscmp(scenario,L"registry")==0)
        return PropertyRegistryAmendmentSmoke() && PropertiesSmoke();
    if (std::wcscmp(scenario,L"withdrawn")==0)
        return FindPropertyRule(1015)==nullptr && FindPropertyRule(1016)==nullptr &&
            FindPropertyRule(1017)==nullptr && FindPropertyRule(7103)==nullptr;
    if (std::wcscmp(scenario,L"optional-absence")==0) {
        const PropertyRule* const optional=FindPropertyRule(7104);
        return optional!=nullptr && MissingPropertySatisfiesCompleteness(*optional,true) &&
            ObservationSatisfiesCompleteness(*optional,ObservationState::NotExposed,true);
    }
    if (std::wcscmp(scenario,L"required-missing")==0) {
        const PropertyRule* const required=FindPropertyRule(5015);
        return required!=nullptr && !MissingPropertySatisfiesCompleteness(*required,true) &&
            !ObservationSatisfiesCompleteness(*required,ObservationState::ReadFailed,true) &&
            ObservationSatisfiesCompleteness(*required,ObservationState::NotApplicable,false);
    }
    return false;
}

bool DocumentGraphSchemaSmoke() {
    bool passed=true;
    passed=Expect(NumericTagsSmoke(),L"numeric tags") && passed;
    passed=Expect(FieldTablesSmoke(),L"record/node/locator field golden tables") && passed;
    passed=Expect(GrammarAndValueSmoke(),L"FieldV1, observations, widths, UTF-16 and ranges") && passed;
    passed=Expect(ProfilesParentsAndEdgesSmoke(),L"profiles, parents and directed edges") && passed;
    passed=Expect(PropertiesSmoke(),L"PropertyKey registry golden") && passed;
    const bool propertyOriginMatrix = PropertyOriginStateMatrixSmoke();
    passed=Expect(propertyOriginMatrix,L"typed effective property origin/state matrix") && passed;
    std::wcout << L"PROPERTY_ORIGIN_CODEC_STATE_MATRIX "
               << propertyOriginMatrix << L'\n';
    passed=Expect(PropertyRegistryAmendmentSmoke(),L"measured PropertyKey schema-v1 amendment") && passed;
    passed=Expect(kCanonicalAssetChunkBytesV1==UINT64_C(1048576) &&
        static_cast<std::uint8_t>(CaptureBlockPhase::Remaps)==6 &&
        static_cast<std::uint8_t>(NodeBlockPhase::AssetChunks)==5 &&
        static_cast<std::uint8_t>(DeduplicationKey::BinaryLengthAndSha256)==1,
        L"canonical order and deduplication") && passed;
    return passed;
}
