#include "scripts/spinner.h"



namespace test_project::scripts {

    void spinner::on_update(GLT::script::script_context& /*ctx*/, f32 dt) {

        angle = std::fmod(angle + degrees_per_second * dt, 360.0f);
    }


    void spinner::reset(GLT::script::script_context& /*ctx*/) { angle = 0.0f; }

}
