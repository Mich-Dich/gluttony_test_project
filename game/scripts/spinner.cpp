#include "scripts/spinner.h"

#include <world/i_world.h>



namespace test_project::scripts {

    void spinner::on_start(GLT::script::script_context& ctx) {

        if (GLT::script::was_restored(ctx.get_flags()))
            LOG(info, "spinner on entity {}: back after a reload at {:.1f} degrees", ctx.entity().index, angle);
    }


    void spinner::on_update(GLT::script::script_context& ctx, f32 dt) {

        const f32 step = degrees_per_second * dt;
        angle = std::fmod(angle + step, 360.0f);
        if (auto* world = ctx.world(); world && glm::length(axis) > 0.0f)
            world->rotate_local(ctx.entity(), glm::normalize(axis) * glm::radians(step));
    }


    void spinner::reset(GLT::script::script_context& /*ctx*/) { angle = 0.0f; }

}
