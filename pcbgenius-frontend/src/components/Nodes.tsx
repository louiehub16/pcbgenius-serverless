import { memo } from "react";
import { Handle, Position, NodeProps } from "@xyflow/react";
import { Component, Net } from "../contractTypes";

/**
 * Component node — renders a contract Component with one source Handle per pin.
 * Handles use id `${ref}.${pin.name}` so edges resolve to the exact pin.
 */

export const ComponentNode = memo(
  ({ data, selected }: NodeProps<{ component: Component }>) => {
    const comp = data.component;
    return (
      <div className={`node component-node ${selected ? "selected" : ""}`}>
        <Handle type="target" position={Position.Left} />
        <div className="node-title">
          <span className="node-ref">{comp.ref}</span>
          <span className="node-type">{comp.type}</span>
        </div>
        <div className="node-value">{comp.value}</div>
        <div
          className="node-sub"
          title={comp.package ?? ""}
        >{`@ ${comp.package}`}</div>
        {comp.pins.map((pin) => (
          <div className="pin-row" key={`${comp.ref}.${pin.name}`}>
            <span className="pin-net" title={pin.net}>
              {pin.net}
            </span>
            <span className="pin-name">{pin.name}</span>
            <Handle
              id={`${comp.ref}.${pin.name}`}
              type="source"
              position={Position.Right}
              className="pin-handle"
            />
          </div>
        ))}
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }
);

/**
 * Net node — a pill representing one contract Net. All pins connected to this net
 * in the model converge here, which is exactly what the frontend renders on screen.
 */
export const NetNode = memo(({ data }: NodeProps<{ net: Net }>) => {
  const net = data.net;
  return (
    <div className="node net-node">
      <Handle type="target" position={Position.Top} />
      <div className="net-name">{net.name}</div>
      <div className="net-class">{net.class}</div>
      <div className="net-count">{net.pins.length} pin{net.pins.length === 1 ? "" : "s"}</div>
    </div>
  );
});