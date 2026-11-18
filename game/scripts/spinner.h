#pragma once

#include "scripting/i_script.h"
#include "scripting/script_context.h"
#include "reflection/annotations.h"

// Example entity script: turns its entity around the up axis. Registered automatically (it is below namespace
// test_project, derives from i_script and has a kind annotation)



namespace test_project::scripts {

    namespace ann = GLT::reflect::annotations;

    struct [[=ann::entity_script{}]] spinner : GLT::script::i_script {

        [[=ann::editor{"Spin"}]] [[=ann::range{0.0, 720.0}]]
        f32                                             degrees_per_second = 45.0f;

        [[=ann::persist{}]]
        f32                                             angle = 0.0f;

        void on_update(GLT::script::script_context& ctx, f32 dt) override;

        [[=ann::editor_callable{"Reset angle"}]]
        void reset(GLT::script::script_context& ctx);
    };

}
