#pragma once

#include "ReferenceLayoutPayloadArrays.h"
#include "ReferenceLayoutSpec.h"

namespace hancom::reference_layout {

bool ParseProtectedGaps(
    const detail::PayloadArrays& arrays,
    Spec* spec,
    actions::Error* error);

bool ValidateProtectedGaps(
    const Spec& spec,
    actions::Error* error);

}
