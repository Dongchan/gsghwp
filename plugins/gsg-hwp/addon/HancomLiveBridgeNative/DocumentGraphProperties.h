#pragma once

#include "DocumentGraphSchema.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace hancom::graph {

enum class PropertyValueShape : std::uint8_t {
    Scalar = 0, NumberingLevelArray = 1, TabItemArray = 2,
    ColumnWidthArray = 3, Uint16Array = 4,
};
enum class RequirementClass : std::uint8_t {
    RequiredWhenApplicable = 0, OptionalObserved = 1,
};
enum class QualifierFlags : std::uint8_t {
    None = 0, VersionGated = 1, OwnerQualificationRequired = 2,
    WindowsBrushFillCondition = 4,
};
constexpr QualifierFlags operator|(const QualifierFlags left, const QualifierFlags right) noexcept {
    return static_cast<QualifierFlags>(static_cast<std::uint8_t>(left) |
                                       static_cast<std::uint8_t>(right));
}
constexpr bool HasQualifier(const QualifierFlags flags,
                            const QualifierFlags qualifier) noexcept {
    return (static_cast<std::uint8_t>(flags) &
            static_cast<std::uint8_t>(qualifier)) != 0;
}
enum class RegistryOrigin : std::uint8_t { Direct = 0, Effective = 1, Generated = 2 };

inline constexpr std::array<PropertyOrigin, 9> kEffectiveValueOriginsV1{{
    PropertyOrigin::Direct,
    PropertyOrigin::Inherited,
    PropertyOrigin::Unknown,
    PropertyOrigin::UserOverride,
    PropertyOrigin::LocalStyle,
    PropertyOrigin::NamedStyle,
    PropertyOrigin::DocumentDefault,
    PropertyOrigin::ImplicitDefault,
    PropertyOrigin::Unavailable,
}};

constexpr bool PropertyOriginAllowedForObservation(
    const RegistryOrigin registryOrigin,
    const ObservationState state,
    const PropertyOrigin origin) noexcept {
    if (registryOrigin == RegistryOrigin::Direct) {
        return origin == PropertyOrigin::Direct ||
            origin == PropertyOrigin::Unknown ||
            (state != ObservationState::Value &&
             (origin == PropertyOrigin::NotApplicable ||
              origin == PropertyOrigin::Unavailable));
    }
    if (registryOrigin == RegistryOrigin::Generated) {
        return origin == PropertyOrigin::Generated ||
            (state != ObservationState::Value &&
             (origin == PropertyOrigin::NotApplicable ||
              origin == PropertyOrigin::Unavailable));
    }
    if (state == ObservationState::NotApplicable) {
        return origin == PropertyOrigin::NotApplicable ||
            origin == PropertyOrigin::Direct ||
            origin == PropertyOrigin::Inherited ||
            origin == PropertyOrigin::Unknown;
    }
    if (origin == PropertyOrigin::NotApplicable) {
        return false;
    }
    if (state != ObservationState::Value) {
        return origin == PropertyOrigin::Unavailable ||
            origin == PropertyOrigin::Direct ||
            origin == PropertyOrigin::Inherited ||
            origin == PropertyOrigin::Unknown;
    }
    for (const PropertyOrigin allowed : kEffectiveValueOriginsV1) {
        if (origin == allowed) return true;
    }
    return false;
}

enum class PropertySourceContract : std::uint8_t {
    DirectValue = 0, LowHighUint32 = 1,
};
constexpr std::uint64_t CombinePropertyTimeLowHigh(
    const std::uint32_t low, const std::uint32_t high) noexcept {
    return static_cast<std::uint64_t>(low) |
           (static_cast<std::uint64_t>(high) << 32U);
}
enum class PropertyApplicabilitySet : std::uint8_t {
    CharacterShape = 0, ParagraphShape = 1, StyleDefinition = 2,
    NumberingDefinition = 3, BorderFillDefinition = 4, TabDefDefinition = 5,
    PageDefDefinition = 6, ColumnDefDefinition = 7, Control = 8, Table = 9,
    Cell = 10, Image = 11, ImageBinary = 12, LayoutAll = 13,
    LayoutControl = 14, DocumentMetadata = 15,
};

struct PropertyRule final {
    PropertyKeyId id;
    PropertyDomain domain;
    const wchar_t* nativeMemberPath;
    ScalarTag scalar;
    PropertyValueShape valueShape;
    PropertyApplicabilitySet applicabilitySet;
    RegistryOrigin origin;
    std::uint64_t profileBits;
    RootClass root;
    Writability writability;
    RequirementClass requirement = RequirementClass::RequiredWhenApplicable;
    QualifierFlags qualifiers = QualifierFlags::None;
    PropertySourceContract sourceContract = PropertySourceContract::DirectValue;
    const wchar_t* nativeLowMemberPath = nullptr;
    const wchar_t* nativeHighMemberPath = nullptr;
};

constexpr std::uint32_t NodeKindBit(const NodeKind kind) noexcept {
    return std::uint32_t{1} << static_cast<std::uint16_t>(kind);
}

struct PropertyApplicabilityTuple final {
    NodeKind node;
    FieldTag ownerNodeFieldTag;
    bool definitionRestricted;
    DefinitionKind definitionKind;
};
struct PropertyApplicabilityRule final {
    PropertyApplicabilitySet set;
    std::array<PropertyApplicabilityTuple, 5> tuples;
    std::size_t tupleCount;
};
inline constexpr std::array<PropertyApplicabilityRule, 16> kPropertyApplicabilityRulesV1{{
    {PropertyApplicabilitySet::CharacterShape,{{{NodeKind::CharacterRun,102,false,DefinitionKind::Style},{NodeKind::Paragraph,104,false,DefinitionKind::Style},{NodeKind::Definition,102,true,DefinitionKind::CharacterShape}}},3},
    {PropertyApplicabilitySet::ParagraphShape,{{{NodeKind::Paragraph,103,false,DefinitionKind::Style},{NodeKind::Definition,102,true,DefinitionKind::ParagraphShape}}},2},
    {PropertyApplicabilitySet::StyleDefinition,{{{NodeKind::Definition,102,true,DefinitionKind::Style}}},1},
    {PropertyApplicabilitySet::NumberingDefinition,{{{NodeKind::Definition,102,true,DefinitionKind::Numbering}}},1},
    {PropertyApplicabilitySet::BorderFillDefinition,{{{NodeKind::Definition,102,true,DefinitionKind::BorderFill}}},1},
    {PropertyApplicabilitySet::TabDefDefinition,{{{NodeKind::Definition,102,true,DefinitionKind::TabDef}}},1},
    {PropertyApplicabilitySet::PageDefDefinition,{{{NodeKind::Definition,102,true,DefinitionKind::PageDef}}},1},
    {PropertyApplicabilitySet::ColumnDefDefinition,{{{NodeKind::Definition,102,true,DefinitionKind::ColumnDef}}},1},
    {PropertyApplicabilitySet::Control,{{{NodeKind::GenericControl,106,false,DefinitionKind::Style},{NodeKind::Table,106,false,DefinitionKind::Style},{NodeKind::Image,106,false,DefinitionKind::Style}}},3},
    {PropertyApplicabilitySet::Table,{{{NodeKind::Table,106,false,DefinitionKind::Style}}},1},
    {PropertyApplicabilitySet::Cell,{{{NodeKind::TableCell,109,false,DefinitionKind::Style}}},1},
    {PropertyApplicabilitySet::Image,{{{NodeKind::Image,112,false,DefinitionKind::Style}}},1},
    {PropertyApplicabilitySet::ImageBinary,{{{NodeKind::Image,11,false,DefinitionKind::Style}}},1},
    {PropertyApplicabilitySet::LayoutAll,{{{NodeKind::Paragraph,10,false,DefinitionKind::Style},{NodeKind::GenericControl,10,false,DefinitionKind::Style},{NodeKind::Table,10,false,DefinitionKind::Style},{NodeKind::TableCell,10,false,DefinitionKind::Style},{NodeKind::Image,10,false,DefinitionKind::Style}}},5},
    {PropertyApplicabilitySet::LayoutControl,{{{NodeKind::GenericControl,10,false,DefinitionKind::Style},{NodeKind::Table,10,false,DefinitionKind::Style},{NodeKind::Image,10,false,DefinitionKind::Style}}},3},
    {PropertyApplicabilitySet::DocumentMetadata,{{{NodeKind::Document,100,false,DefinitionKind::Style}}},1},
}};

inline constexpr std::array<PropertyRule, 165> kPropertyRegistryV1{{
    {1000,PropertyDomain::CharacterShape,L"HCharShape.FaceNameHangul",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1001,PropertyDomain::CharacterShape,L"HCharShape.FaceNameLatin",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1002,PropertyDomain::CharacterShape,L"HCharShape.FaceNameHanja",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1003,PropertyDomain::CharacterShape,L"HCharShape.FaceNameJapanese",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1004,PropertyDomain::CharacterShape,L"HCharShape.FaceNameOther",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1005,PropertyDomain::CharacterShape,L"HCharShape.FaceNameSymbol",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1006,PropertyDomain::CharacterShape,L"HCharShape.FaceNameUser",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1007,PropertyDomain::CharacterShape,L"HCharShape.Height",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1008,PropertyDomain::CharacterShape,L"HCharShape.Bold",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1009,PropertyDomain::CharacterShape,L"HCharShape.Italic",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1010,PropertyDomain::CharacterShape,L"HCharShape.TextColor",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1011,PropertyDomain::CharacterShape,L"HCharShape.ShadeColor",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1012,PropertyDomain::CharacterShape,L"HCharShape.UnderlineType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1013,PropertyDomain::CharacterShape,L"HCharShape.UnderlineColor",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1014,PropertyDomain::CharacterShape,L"HCharShape.StrikeOutType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1018,PropertyDomain::CharacterShape,L"HCharShape.OutlineType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1019,PropertyDomain::CharacterShape,L"HCharShape.ShadowType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {1020,PropertyDomain::CharacterShape,L"HCharShape.SpacingHangul",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1021,PropertyDomain::CharacterShape,L"HCharShape.SpacingLatin",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1022,PropertyDomain::CharacterShape,L"HCharShape.SpacingHanja",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1023,PropertyDomain::CharacterShape,L"HCharShape.SpacingJapanese",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1024,PropertyDomain::CharacterShape,L"HCharShape.SpacingOther",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1025,PropertyDomain::CharacterShape,L"HCharShape.SpacingSymbol",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1026,PropertyDomain::CharacterShape,L"HCharShape.SpacingUser",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1027,PropertyDomain::CharacterShape,L"HCharShape.RatioHangul",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1028,PropertyDomain::CharacterShape,L"HCharShape.RatioLatin",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1029,PropertyDomain::CharacterShape,L"HCharShape.RatioHanja",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1030,PropertyDomain::CharacterShape,L"HCharShape.RatioJapanese",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1031,PropertyDomain::CharacterShape,L"HCharShape.RatioOther",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1032,PropertyDomain::CharacterShape,L"HCharShape.RatioSymbol",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1033,PropertyDomain::CharacterShape,L"HCharShape.RatioUser",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1034,PropertyDomain::CharacterShape,L"HCharShape.OffsetHangul",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1035,PropertyDomain::CharacterShape,L"HCharShape.OffsetLatin",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1036,PropertyDomain::CharacterShape,L"HCharShape.OffsetHanja",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1037,PropertyDomain::CharacterShape,L"HCharShape.OffsetJapanese",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1038,PropertyDomain::CharacterShape,L"HCharShape.OffsetOther",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1039,PropertyDomain::CharacterShape,L"HCharShape.OffsetSymbol",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {1040,PropertyDomain::CharacterShape,L"HCharShape.OffsetUser",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::CharacterShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {2000,PropertyDomain::ParagraphShape,L"HParaShape.AlignType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2001,PropertyDomain::ParagraphShape,L"HParaShape.LineSpacingType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2002,PropertyDomain::ParagraphShape,L"HParaShape.LineSpacing",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2003,PropertyDomain::ParagraphShape,L"HParaShape.LeftMargin",ScalarTag::RawURC32,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2004,PropertyDomain::ParagraphShape,L"HParaShape.RightMargin",ScalarTag::RawURC32,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2005,PropertyDomain::ParagraphShape,L"HParaShape.Indentation",ScalarTag::RawURC32,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2006,PropertyDomain::ParagraphShape,L"HParaShape.PrevSpacing",ScalarTag::RawURC32,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2007,PropertyDomain::ParagraphShape,L"HParaShape.NextSpacing",ScalarTag::RawURC32,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2008,PropertyDomain::ParagraphShape,L"HParaShape.KeepWithNext",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2009,PropertyDomain::ParagraphShape,L"HParaShape.KeepLinesTogether",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2010,PropertyDomain::ParagraphShape,L"HParaShape.PagebreakBefore",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2011,PropertyDomain::ParagraphShape,L"HParaShape.WidowOrphan",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2012,PropertyDomain::ParagraphShape,L"HParaShape.HeadingType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {2013,PropertyDomain::ParagraphShape,L"HParaShape.Level",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::ParagraphShape,RegistryOrigin::Effective,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {3000,PropertyDomain::Style,L"HStyle.Name",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::StyleDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {3001,PropertyDomain::Style,L"HStyle.Type",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::StyleDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {3002,PropertyDomain::Style,L"HStyle.NextStyle",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::StyleDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {3003,PropertyDomain::Style,L"HStyle.CharShape",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::StyleDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {3004,PropertyDomain::Style,L"HStyle.ParaShape",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::StyleDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {4000,PropertyDomain::Numbering,L"HNumbering.StartNumber",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::NumberingDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {4001,PropertyDomain::Numbering,L"HNumbering.Level[]",ScalarTag::Struct,PropertyValueShape::NumberingLevelArray,PropertyApplicabilitySet::NumberingDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {5000,PropertyDomain::BorderFill,L"HCellBorderFill.BorderTypeLeft",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5001,PropertyDomain::BorderFill,L"HCellBorderFill.BorderWidthLeft",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5002,PropertyDomain::BorderFill,L"HCellBorderFill.BorderCorlorLeft",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5003,PropertyDomain::BorderFill,L"HCellBorderFill.BorderTypeRight",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5004,PropertyDomain::BorderFill,L"HCellBorderFill.BorderWidthRight",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5005,PropertyDomain::BorderFill,L"HCellBorderFill.BorderColorRight",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5006,PropertyDomain::BorderFill,L"HCellBorderFill.BorderTypeTop",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5007,PropertyDomain::BorderFill,L"HCellBorderFill.BorderWidthTop",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5008,PropertyDomain::BorderFill,L"HCellBorderFill.BorderColorTop",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5009,PropertyDomain::BorderFill,L"HCellBorderFill.BorderTypeBottom",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5010,PropertyDomain::BorderFill,L"HCellBorderFill.BorderWidthBottom",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5011,PropertyDomain::BorderFill,L"HCellBorderFill.BorderColorBottom",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5012,PropertyDomain::BorderFill,L"HCellBorderFill.FillAttr.WinBrushFaceColor",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5013,PropertyDomain::BorderFill,L"HCellBorderFill.FillAttr.WinBrushHatchColor",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5014,PropertyDomain::BorderFill,L"HCellBorderFill.FillAttr.WinBrushAlpha",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {5015,PropertyDomain::BorderFill,L"HCellBorderFill.FillAttr.WindowsBrush",ScalarTag::Uint8,PropertyValueShape::Scalar,PropertyApplicabilitySet::BorderFillDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly,RequirementClass::RequiredWhenApplicable,QualifierFlags::WindowsBrushFillCondition},
    {6000,PropertyDomain::TabDef,L"HTabDef.AutoTabLeft",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::TabDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {6001,PropertyDomain::TabDef,L"HTabDef.AutoTabRight",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::TabDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {6002,PropertyDomain::TabDef,L"HTabDef.TabItem[]",ScalarTag::Struct,PropertyValueShape::TabItemArray,PropertyApplicabilitySet::TabDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableText),RootClass::Semantic,Writability::ReadOnly},
    {7000,PropertyDomain::PageSetup,L"HPageDef.PaperWidth",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7001,PropertyDomain::PageSetup,L"HPageDef.PaperHeight",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7002,PropertyDomain::PageSetup,L"HPageDef.LeftMargin",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7003,PropertyDomain::PageSetup,L"HPageDef.RightMargin",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7004,PropertyDomain::PageSetup,L"HPageDef.TopMargin",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7005,PropertyDomain::PageSetup,L"HPageDef.BottomMargin",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7006,PropertyDomain::PageSetup,L"HPageDef.HeaderLen",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7007,PropertyDomain::PageSetup,L"HPageDef.FooterLen",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7008,PropertyDomain::PageSetup,L"HPageDef.GutterLen",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7009,PropertyDomain::PageSetup,L"HPageDef.Landscape",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7010,PropertyDomain::PageSetup,L"HPageDef.GutterType",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::PageDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7100,PropertyDomain::Section,L"HColDef.Count",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::ColumnDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7101,PropertyDomain::Section,L"HColDef.SameSize",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::ColumnDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7102,PropertyDomain::Section,L"HColDef.SameGap",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::ColumnDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly},
    {7104,PropertyDomain::Section,L"HColDef.WidthGap[]",ScalarTag::Uint16,PropertyValueShape::Uint16Array,PropertyApplicabilitySet::ColumnDefDefinition,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure) | ProfileBit(ProfileId::Layout),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::VersionGated | QualifierFlags::OwnerQualificationRequired},
    {8000,PropertyDomain::Control,L"HShapeObject.Width",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8001,PropertyDomain::Control,L"HShapeObject.Height",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8002,PropertyDomain::Control,L"HShapeObject.TreatAsChar",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8003,PropertyDomain::Control,L"HShapeObject.TextWrap",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8004,PropertyDomain::Control,L"HShapeObject.VertRelTo",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8005,PropertyDomain::Control,L"HShapeObject.HorzRelTo",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8006,PropertyDomain::Control,L"HShapeObject.VertAlign",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8007,PropertyDomain::Control,L"HShapeObject.HorzAlign",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8008,PropertyDomain::Control,L"HShapeObject.VertOffset",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8009,PropertyDomain::Control,L"HShapeObject.HorzOffset",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8010,PropertyDomain::Control,L"HShapeObject.ZOrder",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8011,PropertyDomain::Control,L"HShapeObject.MarginLeft",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8012,PropertyDomain::Control,L"HShapeObject.MarginRight",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8013,PropertyDomain::Control,L"HShapeObject.MarginTop",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {8014,PropertyDomain::Control,L"HShapeObject.MarginBottom",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Control,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {9000,PropertyDomain::Table,L"TablePropertyDialog.RowCount",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Table,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {9001,PropertyDomain::Table,L"TablePropertyDialog.ColCount",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Table,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {9002,PropertyDomain::Table,L"HShapeObject.CellSpacing",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Table,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {9003,PropertyDomain::Table,L"HShapeObject.PageBreak",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Table,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {9004,PropertyDomain::Table,L"HShapeObject.RepeatHeader",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::Table,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10000,PropertyDomain::Cell,L"CellShape.Width",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10001,PropertyDomain::Cell,L"CellShape.Height",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10002,PropertyDomain::Cell,L"ShapeTableCell.MarginLeft",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10003,PropertyDomain::Cell,L"ShapeTableCell.MarginRight",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10004,PropertyDomain::Cell,L"ShapeTableCell.MarginTop",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10005,PropertyDomain::Cell,L"ShapeTableCell.MarginBottom",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10006,PropertyDomain::Cell,L"ShapeTableCell.VertAlign",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10007,PropertyDomain::Cell,L"ShapeTableCell.Protect",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10008,PropertyDomain::Cell,L"CellFill.WinBrushFaceColor",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10009,PropertyDomain::Cell,L"CellFill.WinBrushFaceStyle",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10010,PropertyDomain::Cell,L"CellBorder.Left.Type",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10011,PropertyDomain::Cell,L"CellBorder.Left.Width",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10012,PropertyDomain::Cell,L"CellBorder.Left.Color",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10013,PropertyDomain::Cell,L"CellBorder.Right.Type",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10014,PropertyDomain::Cell,L"CellBorder.Right.Width",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10015,PropertyDomain::Cell,L"CellBorder.Right.Color",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10016,PropertyDomain::Cell,L"CellBorder.Top.Type",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10017,PropertyDomain::Cell,L"CellBorder.Top.Width",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10018,PropertyDomain::Cell,L"CellBorder.Top.Color",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10019,PropertyDomain::Cell,L"CellBorder.Bottom.Type",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10020,PropertyDomain::Cell,L"CellBorder.Bottom.Width",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10021,PropertyDomain::Cell,L"CellBorder.Bottom.Color",ScalarTag::BGR,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10022,PropertyDomain::Cell,L"CellText.ParagraphAlignment",ScalarTag::Enum,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10023,PropertyDomain::Cell,L"CellText.FaceName",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10024,PropertyDomain::Cell,L"CellText.CharacterHeight",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {10025,PropertyDomain::Cell,L"CellText.Bold",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::Cell,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11000,PropertyDomain::Image,L"ShapeDrawImageAttr.OriginalSizeX",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11001,PropertyDomain::Image,L"ShapeDrawImageAttr.OriginalSizeY",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11002,PropertyDomain::Image,L"ShapeDrawImageAttr.SkipLeft",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11003,PropertyDomain::Image,L"ShapeDrawImageAttr.SkipRight",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11004,PropertyDomain::Image,L"ShapeDrawImageAttr.SkipTop",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11005,PropertyDomain::Image,L"ShapeDrawImageAttr.SkipBottom",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11006,PropertyDomain::Image,L"HShapeObject.Rotation",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11007,PropertyDomain::Image,L"HShapeObject.FlipHorizontal",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11008,PropertyDomain::Image,L"HShapeObject.FlipVertical",ScalarTag::Bool,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11009,PropertyDomain::Image,L"ShapeDrawImageAttr.Transparency",ScalarTag::Sint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::Image,RegistryOrigin::Direct,ProfileBit(ProfileId::EditableObjects),RootClass::Semantic,Writability::ReadOnly},
    {11010,PropertyDomain::Image,L"ShapeDrawImageAttr.FileName",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::ImageBinary,RegistryOrigin::Direct,ProfileBit(ProfileId::BinaryContent),RootClass::Capture,Writability::ReadOnly},
    {12000,PropertyDomain::Layout,L"Hwp.CurrentPage@NodeStart",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::LayoutAll,RegistryOrigin::Generated,ProfileBit(ProfileId::Layout),RootClass::Layout,Writability::ReadOnly},
    {12001,PropertyDomain::Layout,L"Hwp.CurrentPage@NodeEnd",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::LayoutAll,RegistryOrigin::Generated,ProfileBit(ProfileId::Layout),RootClass::Layout,Writability::ReadOnly},
    {12002,PropertyDomain::Layout,L"HShapeObject.Width@Readback",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::LayoutControl,RegistryOrigin::Generated,ProfileBit(ProfileId::Layout),RootClass::Layout,Writability::ReadOnly},
    {12003,PropertyDomain::Layout,L"HShapeObject.Height@Readback",ScalarTag::HWPUNIT64,PropertyValueShape::Scalar,PropertyApplicabilitySet::LayoutControl,RegistryOrigin::Generated,ProfileBit(ProfileId::Layout),RootClass::Layout,Writability::ReadOnly},
    {13000,PropertyDomain::DocumentMetadata,L"SummaryInfo.Title",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {13001,PropertyDomain::DocumentMetadata,L"SummaryInfo.Subject",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {13002,PropertyDomain::DocumentMetadata,L"SummaryInfo.Author",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {13003,PropertyDomain::DocumentMetadata,L"SummaryInfo.Date",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {13004,PropertyDomain::DocumentMetadata,L"SummaryInfo.KeyWords",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {13005,PropertyDomain::DocumentMetadata,L"SummaryInfo.Comments",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Semantic,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {13006,PropertyDomain::DocumentMetadata,L"SummaryInfo.CreationTime",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Capture,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None,PropertySourceContract::LowHighUint32,L"SummaryInfo.CreationTimeLow",L"SummaryInfo.CreationTimeHigh"},
    {13007,PropertyDomain::DocumentMetadata,L"SummaryInfo.ModifiedTime",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Capture,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None,PropertySourceContract::LowHighUint32,L"SummaryInfo.ModifiedTimeLow",L"SummaryInfo.ModifiedTimeHigh"},
    {13008,PropertyDomain::DocumentMetadata,L"SummaryInfo.PrintedTime",ScalarTag::Uint64,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Capture,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None,PropertySourceContract::LowHighUint32,L"SummaryInfo.PrintedTimeLow",L"SummaryInfo.PrintedTimeHigh"},
    {13009,PropertyDomain::DocumentMetadata,L"SummaryInfo.LastSavedBy",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Capture,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
    {13010,PropertyDomain::DocumentMetadata,L"SummaryInfo.DocVersion",ScalarTag::UTF16,PropertyValueShape::Scalar,PropertyApplicabilitySet::DocumentMetadata,RegistryOrigin::Direct,ProfileBit(ProfileId::Structure),RootClass::Capture,Writability::ReadOnly,RequirementClass::OptionalObserved,QualifierFlags::None},
}};

inline constexpr std::array<PropertyKeyId, 4> kWithdrawnPropertyKeyIdsV1{{1015,1016,1017,7103}};
struct PropertyAliasRule final { const wchar_t* aliasMemberPath; PropertyKeyId canonicalId; };
inline constexpr std::array<PropertyAliasRule,4> kPropertyAliasesV1{{
    {L"HCharShape.OutLineType",1018},
    {L"HParaShape.KeepLines",2009},
    {L"HParaShape.PageBreakBefore",2010},
    {L"HCellBorderFill.BorderColorLeft",5002},
}};

constexpr const PropertyRule* FindPropertyRule(const PropertyKeyId id) noexcept {
    for (const PropertyRule& rule : kPropertyRegistryV1) if (rule.id == id) return &rule;
    return nullptr;
}

constexpr bool WideEqual(const wchar_t* left, const wchar_t* right) noexcept {
    if (left == nullptr || right == nullptr) return false;
    while (*left != L'\0' && *left == *right) { ++left; ++right; }
    return *left == *right;
}
constexpr const PropertyRule* FindPropertyRuleByMemberPath(const wchar_t* path) noexcept {
    for (const PropertyRule& rule : kPropertyRegistryV1)
        if (WideEqual(rule.nativeMemberPath,path)) return &rule;
    for (const PropertyAliasRule& alias : kPropertyAliasesV1)
        if (WideEqual(alias.aliasMemberPath,path)) return FindPropertyRule(alias.canonicalId);
    return nullptr;
}
constexpr bool MissingPropertySatisfiesCompleteness(
    const PropertyRule& rule, const bool applicable) noexcept {
    return !applicable || rule.requirement == RequirementClass::OptionalObserved;
}
constexpr bool ObservationSatisfiesCompleteness(
    const PropertyRule& rule, const ObservationState state,
    const bool applicable) noexcept {
    if (!applicable) return true;
    if (rule.requirement == RequirementClass::OptionalObserved) return true;
    return state == ObservationState::Value;
}

constexpr const PropertyApplicabilityRule* FindPropertyApplicabilityRule(
    const PropertyApplicabilitySet set) noexcept {
    for (const PropertyApplicabilityRule& rule : kPropertyApplicabilityRulesV1) {
        if (rule.set == set) return &rule;
    }
    return nullptr;
}

constexpr bool IsPropertyApplicable(
    const PropertyRule& rule, const NodeKind node, const FieldTag ownerField,
    const bool hasDefinitionKind = false,
    const DefinitionKind definitionKind = DefinitionKind::Style) noexcept {
    const PropertyApplicabilityRule* const applicability =
        FindPropertyApplicabilityRule(rule.applicabilitySet);
    if (applicability == nullptr) return false;
    for (std::size_t index = 0; index < applicability->tupleCount; ++index) {
        const PropertyApplicabilityTuple tuple = applicability->tuples[index];
        if (tuple.node != node || tuple.ownerNodeFieldTag != ownerField) continue;
        if (!tuple.definitionRestricted) return true;
        if (hasDefinitionKind && tuple.definitionKind == definitionKind) return true;
    }
    return false;
}

constexpr bool IsRegistrySortedUnique() noexcept {
    for (std::size_t index = 1; index < kPropertyRegistryV1.size(); ++index) {
        if (kPropertyRegistryV1[index - 1].id >= kPropertyRegistryV1[index].id) return false;
    }
    return true;
}

static_assert(IsRegistrySortedUnique(), "PropertyKey registry must be sorted and unique");
static_assert(kPropertyRegistryV1.size() == 165, "PropertyKey registry row count changed");

} // namespace hancom::graph
