// Example custom tool. DeepSeek Harness tools are Cordis plugins (JavaScript), loaded by
// main.py from this directory. The runtime's own packages can't be imported from a plugin
// outside its bundle, so this registers a plain ToolDefinition instead of using defineTool:
// `parameters` is JSON Schema, and `output.render` turns the return value into tool-result
// content. A thrown error becomes an error result for the model.
export const name = 'current-time-tool'
export const inject = ['tools']

export function apply(ctx) {
  ctx.tools.register({
    name: 'current_time',
    description: 'Get the current date and time in an IANA timezone.',
    parameters: {
      type: 'object',
      properties: {
        timezone: { type: 'string', description: 'IANA timezone, e.g. Asia/Tokyo. Defaults to UTC.' },
      },
      additionalProperties: false,
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args) {
      const timeZone = args?.timezone || 'UTC'
      return new Date().toLocaleString('sv-SE', { timeZone, timeZoneName: 'longOffset' })
    },
  })
}
