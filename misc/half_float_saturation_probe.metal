#include <metal_stdlib>
using namespace metal;

kernel void macws_half_saturation(
        texture2d<float, access::write> output [[texture(0)]],
        uint2 position [[thread_position_in_grid]]) {
    output.write(float4(100000.0f, -100000.0f, 1.0f, 0.0f), position);
}
