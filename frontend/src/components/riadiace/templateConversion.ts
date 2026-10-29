/** DEV-254: a project the conversion onto the app template is for — built without a template, Štandardný (a Web
 *  project is not converted). The offer itself says whether it can be done now. */
export function templateConversionApplies(project: { template?: string | null; type: string }): boolean {
  return !project.template && project.type === "standard";
}
